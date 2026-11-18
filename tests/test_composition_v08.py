"""P7: source-free composition intent, Scene0.3, isolation and honest review."""
import copy
import json
import shutil

import pytest

from scientific_figure.bundles import create_bundle
from scientific_figure.composition import compose
from scientific_figure.composition_context import FILENAME, validate_composition_context
from scientific_figure.context import apply_context, canonical_hash
from scientific_figure.editing import apply_patch, export_scene
from scientific_figure.geometry import Geometry, point
from scientific_figure.project import digest, load_project, project_extras, write_project
from PIL import Image
from scientific_figure.reviews import current_project_review
from scientific_figure.scene import load_json, schema_validate
from scientific_figure.svg import to_svg


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def member_scene(version="0.3"):
    scene = {"schema_version": version, "revision": 0,
             "canvas": {"width": 120, "height": 90, "viewBox": [0, 0, 120, 90], "background": "none"},
             "objects": [
                 {"id": "root", "kind": "group", "transform": [1, 0, 0.1, 1, 0, 0]},
                 {"id": "body", "kind": "rect", "parent": "root",
                  "geometry": {"x": 10, "y": 15, "width": 25, "height": 20},
                  "style": {"fill": "url(#shade)"}},
                 {"id": "right", "kind": "rect", "parent": "root",
                  "geometry": {"x": 65, "y": 15, "width": 25, "height": 20}},
                 {"id": "label", "kind": "text", "parent": "root",
                  "geometry": {"x": 5, "y": 65, "text": "Original long member transcription"},
                  "style": {"font_size": 8}},
                 {"id": "wire", "kind": "connector", "parent": "root",
                  "geometry": {"from": "body", "to": "right"}, "style": {"stroke": "#123456"}}],
             "resources": [{"id": "shade", "kind": "linear_gradient", "start": [0, 0], "end": [1, 0],
                            "stops": [[0, "#123456"], [1, "#abcdef"]]}]}
    if version == "0.3":
        scene["objects"][1]["anchors"] = {"out": [35, 25]}
        scene["objects"][2]["anchors"] = {"in": [65, 25]}
        scene["objects"][4]["geometry"].update(from_anchor="port:out", to_anchor="port:in")
        scene["objects"][4]["style"].update(stroke_dasharray=[3, 2], stroke_dashoffset=1)
        scene["resources"][0] = {"id": "shade", "kind": "radial_gradient", "center": [0.5, 0.5],
                                  "radius": 0.5, "stops": [[0, "#123456"], [1, "#abcdef"]]}
    if version in {"0.2", "0.3"}:
        scene["objects"].extend([
            {"id": "over", "kind": "path", "parent": "root", "geometry": {"d": "M10 72H35V80H10Z"},
             "style": {"fill": "#123456"}},
            {"id": "under", "kind": "path", "parent": "root", "geometry": {"d": "M20 70H28V85H20Z"},
             "style": {"fill": "#abcdef"}}])
        scene["occlusions"] = [{"id": "overlap", "over": "over", "under": "under", "frame": "root",
                                 "region": [15, 68, 20, 19], "gap": 0}]
    return scene


def portable_context(tmp_path, scene):
    profile = {"schema_version": "0.1", "id": "profile", "tokens": [
        {"id": "main", "type": "color", "value": "#123456"}]}
    text = {"schema_version": "0.1", "id": "text", "entries": [
        {"id": "caption", "object_id": "label", "text": "Original long member transcription",
         "uncertainty_ids": ["identity"]}]}
    layout = {"schema_version": "0.1", "id": "layout", "entries": [
        {"id": "reading_order", "object_ids": ["body", "right"], "relation": "left_of"}]}
    context = {"schema_version": "0.1", "id": "context", "purpose": "Member intent",
               "roles": [{"id": "compartment", "description": "Keep the compartment"}],
               "representations": [{"id": "outline", "kind": "boundary", "description": "Boundary"}],
               "uncertainties": [{"id": "identity", "description": "Host must verify"}],
               "objects": [{"object_id": "body", "role_id": "compartment", "representation_id": "outline",
                            "uncertainty_ids": ["identity"], "preserve": True}],
               "preserved_ids": ["body"],
               "token_bindings": [{"object_id": "wire", "field": "stroke", "token": "main"}]}
    for key, document in (("style_profile", profile), ("text_manifest", text), ("layout_manifest", layout)):
        context[key] = {"document": document, "sha256": canonical_hash(document)}
    scene["provenance"] = {"source": {"sha256": "a" * 64, "size": [240, 180], "region": [10, 20, 130, 110]},
                           "brief": {"purpose": "Original semantic intent", "protected_ids": ["body"],
                                     "must_preserve": ["The scientific identity remains host reviewed"]}}
    path = save(tmp_path / "member-context.json", context)
    return apply_context(scene, path)


def bundle_of(tmp_path, scenes, contextual=False):
    records = []
    for index, scene in enumerate(scenes):
        directory = tmp_path / f"member{index}"
        extras = {}
        if contextual:
            scene, extras = portable_context(tmp_path, scene)
            # The original plan is source intent, not a live geometry authority.
            extras["reconstruction-plan.json"] = {"schema_version": "0.3", "brief": scene["provenance"]["brief"],
                                                    "steps": [{"object_id": "body", "description": "Keep boundary"}]}
        write_project(scene, directory, scale=1, extras=extras)
        records.append({"id": f"asset{index}", "project": directory.name})
    create_bundle(save(tmp_path / "bundle-plan.json", {"schema_version": "0.1", "assets": records}), tmp_path / "bundle")
    return tmp_path / "bundle"


def request(version="0.2", same=True):
    return {"schema_version": version, "canvas": {"width": 360, "height": 180, "background": "none"},
            "containers": [{"id": "panel", "kind": "group", "transform": [1, 0.1, 0.2, 1, 10, 5]}],
            "placements": [
                {"asset_id": "asset0", "instance_id": "A", "parent": "panel", "transform": [1, 0, 0, 1, 0, 0]},
                {"asset_id": "asset0" if same else "asset1", "instance_id": "B", "parent": "panel",
                 "transform": [-1, 0, 0.15, 1, 285, 20]}]}


def build(tmp_path, bundle, plan=None, name="composite"):
    output = tmp_path / name
    compose(save(tmp_path / (name + "-plan.json"), plan or request()), bundle, output, scale=1)
    return load_project(output), project_extras(output), output


def nodes(scene):
    return {obj["id"]: obj for obj in scene["objects"]}


def rebind(scene, extras):
    scene["provenance"]["composition"]["context_sha256"] = canonical_hash(extras[FILENAME])


def test_closed_bundle_survives_source_and_bundle_removal_edits_and_export(tmp_path):
    bundle = bundle_of(tmp_path, [member_scene()], contextual=True)
    original, extras, output = build(tmp_path, bundle)
    document = extras[FILENAME]
    assert FILENAME in load_json(output / "receipt.json")["artifacts"]
    assert set(original["provenance"]) == {"mode", "composition"}
    assert not {"context.json", "text-manifest.json", "generation.json"} & extras.keys()
    assert current_project_review(original, output)["visual"]["status"] == "not_executed"
    source = document["members"]["asset0"]
    assert source["scene_sha256"] == source["receipt"]["document"]["artifacts"]["scene.json"]["sha256"]
    assert document["aggregate"]["intent"]
    assert source["provenance"]["document"]["source"]["region"] == [10, 20, 130, 110]
    assert all(entry["value"]["brief"]["must_preserve"] for entry in document["aggregate"]["intent"].values())
    assert len(document["aggregate"]["plans"]) == 2
    for folder in (bundle, tmp_path / "member0"):
        shutil.rmtree(folder)
    (tmp_path / "member-context.json").unlink()
    (tmp_path / "bundle-plan.json").unlink()
    loaded = load_project(output)
    assert loaded == original
    instances = loaded["provenance"]["composition"]["instances"]
    a, b = instances["A"]["id_map"]["objects"], instances["B"]["id_map"]["objects"]
    changed = apply_patch(loaded, {"base_revision": 0, "operations": [
        {"id": a["label"], "set": {"geometry": {"text": "Edited current label"}}},
        {"id": a["right"], "set": {"geometry": {"x": 80}}},
        {"id": a["wire"], "set": {"style": {"stroke": "#ff0000"}}}]})
    write_project(changed, output, scale=1, expected_revision=0)
    assert load_project(output) == changed
    assert project_extras(output)[FILENAME] == document
    assert nodes(changed)[b["label"]] == nodes(original)[b["label"]]
    assert next(iter(document["aggregate"]["text_entries"].values()))["value"]["text"].startswith("Original")
    selected = export_scene(changed, [instances["A"]["wrapper_id"]])
    assert not set(b.values()) & nodes(selected).keys()
    assert "radialGradient" in to_svg(selected)
    assert nodes(selected)[a["wire"]]["kind"] == "path"


def test_duplicate_member_metadata_tokens_and_resources_are_isolated(tmp_path):
    bundle = bundle_of(tmp_path, [member_scene()], contextual=True)
    scene, extras, _ = build(tmp_path, bundle)
    doc = extras[FILENAME]
    maps = doc["metadata_id_maps"]
    for category in ("roles", "representations", "uncertainties", "tokens", "text_entries", "layout_entries", "relations"):
        assert set(maps["A"][category].values()).isdisjoint(maps["B"][category].values())
    instances = doc["instances"]
    a = instances["A"]["id_map"]
    b = instances["B"]["id_map"]
    assert set(a["resources"].values()).isdisjoint(b["resources"].values())
    body = doc["aggregate"]["objects"][a["objects"]["body"]]["value"]
    assert body["role_id"] == maps["A"]["roles"]["compartment"]
    assert body["representation_id"] == maps["A"]["representations"]["outline"]
    assert body["uncertainty_ids"] == [maps["A"]["uncertainties"]["identity"]]
    assert doc["protected_ids"] == sorted([a["objects"]["body"], b["objects"]["body"]])
    token_refs = [entry["value"]["token"] for entry in doc["aggregate"]["token_bindings"].values()]
    assert set(token_refs) == {maps["A"]["tokens"]["main"], maps["B"]["tokens"]["main"]}
    edited = copy.deepcopy(scene)
    resource = next(r for r in edited["resources"] if r["id"] == a["resources"]["shade"])
    resource["stops"][0][1] = "#ff0000"
    validate_composition_context(edited, extras)
    assert next(r for r in edited["resources"] if r["id"] == b["resources"]["shade"])["stops"][0][1] == "#123456"
    assert doc["members"]["asset0"]["provenance"]["document"]["brief"]["protected_ids"] == ["body"]


def test_nested_parent_transforms_named_ports_internal_and_cross_instance_connections(tmp_path):
    bundle = bundle_of(tmp_path, [member_scene()])
    plan = request()
    plan["containers"].append({"id": "port_container", "kind": "ellipse", "parent": "panel",
                               "geometry": {"cx": 20, "cy": 120, "rx": 5, "ry": 5},
                               "anchors": {"north": [20, 115]}, "style": {"stroke_dasharray": [1, 2]}})
    plan["connections"] = [{"id": "bridge", "parent": "panel",
                             "from": {"instance_id": "A", "object_id": "body", "anchor": "port:out"},
                             "to": {"instance_id": "B", "object_id": "right", "anchor": "port:in"},
                             "style": {"stroke": "#000000", "stroke_dasharray": [4, 3]}}]
    scene, extras, output = build(tmp_path, bundle, plan)
    assert scene["schema_version"] == "0.3"
    instances = scene["provenance"]["composition"]["instances"]
    a, b = (instances[i]["id_map"]["objects"] for i in ("A", "B"))
    geo = Geometry(scene)
    assert geo.endpoints("bridge")[0] == pytest.approx(point(geo.world(a["body"]), 35, 25))
    assert geo.endpoints("bridge")[1] == pytest.approx(point(geo.world(b["right"]), 65, 25))
    assert nodes(scene)[a["body"]]["anchors"] == {"out": [35, 25]}
    assert "stroke-dasharray" in (output / "figure.svg").read_text(encoding="utf-8")
    changed = apply_patch(scene, {"base_revision": 0, "operations": [
        {"id": a["body"], "set": {"transform": [1, 0, 0, 1, 7, 3]}}]})
    validate_composition_context(changed, extras)
    assert Geometry(changed).endpoints("bridge")[0] != geo.endpoints("bridge")[0]
    assert Geometry(changed).endpoints("bridge")[1] == geo.endpoints("bridge")[1]
    assert Geometry(changed).endpoints(a["wire"])[0] != geo.endpoints(a["wire"])[0]
    assert Geometry(changed).endpoints(b["wire"]) == geo.endpoints(b["wire"])


@pytest.mark.parametrize("versions", [("0.1", "0.1"), ("0.1", "0.2"), ("0.2", "0.3"), ("0.3", "0.1")])
def test_scene_version_promotes_maximum_independent_of_member_order(tmp_path, versions):
    bundle = bundle_of(tmp_path, [member_scene(version) for version in versions])
    scene, _, _ = build(tmp_path, bundle, request("0.1", same=False))
    assert scene["schema_version"] == max(versions)


@pytest.mark.parametrize("feature", ["port", "dash", "container_port", "alias"])
def test_plan_01_stays_strict_and_explicit_aliases_are_rejected(tmp_path, feature):
    bundle = bundle_of(tmp_path, [member_scene()])
    plan = request("0.1")
    if feature == "port":
        plan["connections"] = [{"id": "wire", "from": {"instance_id": "A", "object_id": "body", "anchor": "port:out"},
                                "to": {"instance_id": "B", "object_id": "right", "anchor": "left"}}]
    elif feature == "dash":
        plan["containers"][0]["style"] = {"stroke_dasharray": [2, 2]}
    elif feature == "container_port":
        plan["containers"][0]["anchors"] = {"x": [1, 2]}
    else:
        plan["schema_version"] = "0.2"
        plan["token_mappings"] = {"A.main": "B.main"}
    with pytest.raises(ValueError):
        build(tmp_path, bundle, plan, name="invalid")
    assert not (tmp_path / "invalid").exists()


@pytest.mark.parametrize("corruption", ["document_sha", "receipt_sha", "scene_sha", "source", "aggregate", "map", "plan_sha"])
def test_context_sha_and_source_receipt_bindings_reject_corruption(tmp_path, corruption):
    bundle = bundle_of(tmp_path, [member_scene()], contextual=True)
    scene, extras, _ = build(tmp_path, bundle)
    bad_scene, bad_extras = copy.deepcopy(scene), copy.deepcopy(extras)
    doc = bad_extras[FILENAME]
    member = doc["members"]["asset0"]
    if corruption == "document_sha":
        member["documents"]["text-manifest.json"]["sha256"] = "0" * 64
    elif corruption == "receipt_sha":
        member["receipt"]["sha256"] = "0" * 64
    elif corruption == "scene_sha":
        member["scene_sha256"] = "0" * 64
    elif corruption == "source":
        member["provenance"]["document"]["source"]["sha256"] = "0" * 64
        member["provenance"]["sha256"] = canonical_hash(member["provenance"]["document"])
    elif corruption == "aggregate":
        next(iter(doc["aggregate"]["text_entries"].values()))["value"]["object_id"] = "missing"
    elif corruption == "map":
        doc["metadata_id_maps"]["A"]["roles"]["compartment"] = "missing"
    else:
        doc["plan"]["sha256"] = "0" * 64
    # Rebind the outer digest to reach the independent inner receipt/ref checks.
    rebind(bad_scene, bad_extras)
    with pytest.raises(ValueError, match="SHA|hash|binding|mismatch"):
        validate_composition_context(bad_scene, bad_extras)


def test_missing_unbound_and_unreceipted_context_are_rejected_on_real_load(tmp_path):
    bundle = bundle_of(tmp_path, [member_scene()])
    scene, extras, output = build(tmp_path, bundle)
    with pytest.raises(ValueError, match="missing composition-context"):
        validate_composition_context(scene, {})
    unbound = copy.deepcopy(scene)
    del unbound["provenance"]["composition"]["context_sha256"]
    with pytest.raises(ValueError, match="binding"):
        validate_composition_context(unbound, extras)
    receipt = load_json(output / "receipt.json")
    del receipt["artifacts"][FILENAME]
    save(output / "receipt.json", receipt)
    with pytest.raises(ValueError, match="unreceipted composition"):
        load_project(output)


@pytest.mark.parametrize("change", ["hidden", "deleted", "text_kind"])
def test_current_references_and_protected_visibility_remain_enforced(tmp_path, change):
    bundle = bundle_of(tmp_path, [member_scene()], contextual=True)
    scene, extras, _ = build(tmp_path, bundle)
    ids = extras[FILENAME]["instances"]["A"]["id_map"]["objects"]
    modified = copy.deepcopy(scene)
    if change == "hidden":
        nodes(modified)[ids["body"]].setdefault("style", {})["fill_opacity"] = 0
    elif change == "deleted":
        modified["objects"] = [obj for obj in modified["objects"] if obj["id"] != ids["label"]]
    else:
        obj = nodes(modified)[ids["label"]]
        obj["kind"], obj["geometry"] = "rect", {"x": 5, "y": 65, "width": 10, "height": 5}
    with pytest.raises(ValueError, match="protected object|metadata object reference|not text"):
        validate_composition_context(modified, extras)


def test_composite_review_never_inherits_member_and_expires_after_edit(tmp_path):
    bundle = bundle_of(tmp_path, [member_scene()], contextual=True)
    member = tmp_path / "member0"
    source = load_project(member)
    host = {"schema_version": "0.3", "binding": current_project_review(source, member)["binding"],
            "reviewer": {"name": "test host", "role": "host"},
            "visual": {"status": "passed", "findings": []}, "scientific": {"status": "passed", "findings": []}}
    write_project(source, member, scale=1, expected_revision=0, extras={"review.json": {"host_record": host}})
    # Rebuild immutable bundle from the now-reviewed member.
    shutil.rmtree(bundle)
    create_bundle(tmp_path / "bundle-plan.json", bundle)
    scene, extras, output = build(tmp_path, bundle)
    snapshot_review = extras[FILENAME]["members"]["asset0"]["documents"]["review.json"]["document"]
    assert snapshot_review["visual"]["status"] == "passed"
    assert current_project_review(scene, output)["visual"]["status"] == "not_executed"
    host["binding"] = current_project_review(scene, output)["binding"]
    write_project(scene, output, scale=1, expected_revision=0, extras={"review.json": {"host_record": host}})
    label = extras[FILENAME]["instances"]["A"]["id_map"]["objects"]["label"]
    changed = apply_patch(scene, {"base_revision": 0, "operations": [
        {"id": label, "set": {"geometry": {"text": "Current revision"}}}]})
    write_project(changed, output, scale=1, expected_revision=0)
    review = current_project_review(load_project(output), output)
    assert review["visual"]["status"] == review["scientific"]["status"] == "stale_after_change"


def test_nested_composition_is_explicitly_rejected_without_output(tmp_path):
    bundle = bundle_of(tmp_path, [member_scene()])
    _, _, output = build(tmp_path, bundle)
    create_bundle(save(tmp_path / "nested-plan.json", {"schema_version": "0.1", "assets": [
        {"id": "asset0", "project": output.name}]}), tmp_path / "nested-bundle")
    with pytest.raises(ValueError, match="nested composition members are unsupported"):
        build(tmp_path, tmp_path / "nested-bundle", name="nested")
    assert not (tmp_path / "nested").exists()


def test_generation_member_snapshot_stays_inert_and_composite_generation_is_unsupported(tmp_path):
    from scientific_figure.generation import attach_generation, generation_input

    pure = member_scene("0.1")
    record = generation_input(pure, language="python", source="raise RuntimeError('never execute')",
                              params={}, backend={"name": "host", "version": "1"})
    source, extras = attach_generation(pure, record)
    write_project(source, tmp_path / "generated", scale=1, extras=extras)
    create_bundle(save(tmp_path / "bundle-plan.json", {"schema_version": "0.1", "assets": [
        {"id": "asset0", "project": "generated"}]}), tmp_path / "bundle")
    scene, documents, _ = build(tmp_path, tmp_path / "bundle", request("0.1"))
    assert "generation.json" in documents[FILENAME]["members"]["asset0"]["documents"]
    assert "generation" not in scene["provenance"]
    assert "generation.json" not in documents
    with pytest.raises(ValueError, match="unsupported.*generation.*provenance"):
        attach_generation(scene, record, baseline=pure)


def test_figure_plan_source_is_portable_intent_without_active_composite_planner_binding(tmp_path):
    from scientific_figure.planning import compile_plan

    plan = {"schema_version": "0.1", "id": "member_plan", "task_type": "text_design",
            "purpose": "Keep original member meaning", "canvas": {"width": 100, "height": 80},
            "modules": [{"id": "module", "objects": [
                {"id": "compartment", "role": "scientific boundary", "must_preserve": ["identity"],
                 "construction": {"kind": "direct", "objects": [
                     {"id": "outline", "kind": "rect",
                      "geometry": {"x": 5, "y": 5, "width": 30, "height": 20}}]}}]}],
            "relations": [], "review_spec": {"must_preserve": ["scientific identity"],
                                               "required_objects": ["compartment"]}}
    source, source_extras, _ = compile_plan(plan)
    write_project(source, tmp_path / "planned", extras=source_extras, scale=1)
    create_bundle(save(tmp_path / "bundle-plan.json", {"schema_version": "0.1", "assets": [
        {"id": "asset0", "project": "planned"}]}), tmp_path / "bundle")
    scene, extras, output = build(tmp_path, tmp_path / "bundle")
    doc = extras[FILENAME]
    assert doc["members"]["asset0"]["documents"]["figure-plan.json"]["document"] == plan
    assert {entry["value"]["member_document"] for entry in doc["aggregate"]["plans"].values()} == {"figure-plan.json"}
    for entry in doc["aggregate"]["plans"].values():
        assert entry["value"]["object_id_map"] == doc["instances"][entry["instance_id"]]["id_map"]["objects"]
    assert "figure-plan.json" not in extras and "generation.json" not in extras
    assert "generation" not in scene["provenance"]
    shutil.rmtree(tmp_path / "planned")
    shutil.rmtree(tmp_path / "bundle")
    assert load_project(output) == scene


def test_member_evidence_source_roi_and_reference_maps_survive_original_removal(tmp_path):
    from scientific_figure.evidence import generate_evidence

    external = tmp_path / "external"
    external.mkdir()
    source_path = external / "source.png"
    Image.new("RGB", (64, 64), "white").save(source_path)
    generate_evidence(source_path, external / "evidence")
    scene = member_scene("0.1")
    scene["provenance"] = {"source": {"sha256": digest(source_path), "size": [64, 64], "region": [0, 0, 64, 64]}}
    context = {"schema_version": "0.1", "id": "evidence_context",
               "evidence": [{"id": "pixels", "path": "external/evidence/evidence.json",
                             "sha256": digest(external / "evidence" / "evidence.json")}],
               "objects": [{"object_id": "body", "evidence_ids": ["pixels"]}]}
    source, source_extras = apply_context(scene, save(tmp_path / "evidence-context.json", context))
    write_project(source, tmp_path / "member", extras=source_extras, scale=1)
    create_bundle(save(tmp_path / "bundle-plan.json", {"schema_version": "0.1", "assets": [
        {"id": "asset0", "project": "member"}]}), tmp_path / "bundle")
    shutil.rmtree(external)
    shutil.rmtree(tmp_path / "member")
    (tmp_path / "evidence-context.json").unlink()
    scene, extras, output = build(tmp_path, tmp_path / "bundle", request("0.1"))
    doc = extras[FILENAME]
    for iid in ("A", "B"):
        body = doc["instances"][iid]["id_map"]["objects"]["body"]
        assert doc["aggregate"]["objects"][body]["value"]["evidence_ids"] == [doc["metadata_id_maps"][iid]["evidence"]["pixels"]]
    evidence = doc["members"]["asset0"]["documents"]["context.json"]["document"]["evidence"][0]
    assert "path" not in evidence["document"]["source"]
    assert evidence["document"]["source"]["roi"] == [0, 0, 64, 64]
    shutil.rmtree(tmp_path / "bundle")
    assert load_project(output) == scene


def test_context_schema_rejects_unrecognized_current_geometry_fields(tmp_path):
    bundle = bundle_of(tmp_path, [member_scene()])
    _, extras, _ = build(tmp_path, bundle)
    document = copy.deepcopy(extras[FILENAME])
    document["current_geometry"] = {"objects": []}
    with pytest.raises(ValueError):
        schema_validate(document, "composition-context")

"""P3 generation boundaries: user ownership, closure, stale state and atomicity."""
import copy
import json
import shutil
import subprocess
import sys

import pytest

from scientific_figure.bundles import create_bundle, validate_bundle
from scientific_figure.editing import apply_patch
from scientific_figure.generation import (accept_generation, attach_generation, diff_generation,
                                          generation_input, validate_generation)
from scientific_figure.project import (digest, json_text, load_project, project_extras,
                                       project_state_sha256, write_project)
from scientific_figure.scene import load_json


def gradient(oid="shade"):
    return {"id": oid, "kind": "linear_gradient", "start": [0, 0], "end": [1, 0],
            "stops": [[0, "#ff0000"], [1, "#0000ff"]]}


def pure_scene():
    return {"schema_version": "0.1", "revision": 0,
            "canvas": {"width": 100, "height": 80, "background": "none"},
            "resources": [gradient()], "objects": [
                {"id": "g", "kind": "group"}, {"id": "h", "kind": "group"},
                {"id": "a", "parent": "g", "kind": "rect",
                 "geometry": {"x": 5, "y": 5, "width": 20, "height": 20, "rx": 2},
                 "style": {"fill": "#000000", "opacity": 0.9}},
                {"id": "b", "parent": "g", "kind": "rect",
                 "geometry": {"x": 30, "y": 5, "width": 20, "height": 20},
                 "style": {"fill": "url(#shade)"}},
                {"id": "p", "parent": "g", "kind": "path", "geometry": {"d": "M 5 40 L 20 40 L 20 50 Z"}},
                {"id": "stable", "kind": "ellipse", "geometry": {"cx": 75, "cy": 40, "rx": 10, "ry": 10}}]}


def node(scene, oid):
    return next(obj for obj in scene["objects"] if obj["id"] == oid)


def record(scene, **kwargs):
    return generation_input(scene, language="python", source="raise RuntimeError('must never run')\n",
                            params={"size": 20}, backend={"name": "host", "version": "1"}, **kwargs)


def save(path, value):
    path.write_text(json_text(value), encoding="utf-8")
    return path


def source(tmp_path, scene, name="source", input_record=None):
    root = tmp_path/name
    root.mkdir()
    save(root/"scene.json", scene)
    save(root/"generation-input.json", input_record or record(scene))
    return root


@pytest.fixture
def project(tmp_path):
    scene = pure_scene()
    attached, extras = attach_generation(scene, record(scene))
    root = tmp_path/"project"
    write_project(attached, root, scale=1, extras=extras)
    return root


def edit(project, change):
    scene = load_project(project)
    previous = scene["revision"]
    change(scene)
    scene["revision"] += 1
    write_project(scene, project, scale=1, expected_revision=previous)
    return scene


def snapshot(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def rerun(tmp_path, project, candidate, name="diff", input_record=None):
    src = source(tmp_path, candidate, "source-"+name, input_record)
    out = tmp_path/name
    result = diff_generation(project, src, out)
    return result, out


def add_user(scene):
    scene["resources"].append(gradient("user_shade"))
    scene["objects"].append({"id": "user", "kind": "rect", "parent": "g",
                             "geometry": {"x": 55, "y": 5, "width": 5, "height": 5},
                             "style": {"fill": "url(#user_shade)"},
                             "semantic": {"peer": {"type": "object_ref", "ref": "b"},
                                          "resource_id": "user_shade"}})


def move_a(scene):
    node(scene, "a")["transform"] = [1, 0, 0, 1, 7, 3]


def user_order(scene):
    a, b = scene["objects"].index(node(scene, "a")), scene["objects"].index(node(scene, "b"))
    scene["objects"][a], scene["objects"][b] = scene["objects"][b], scene["objects"][a]


@pytest.mark.parametrize("change", [
    lambda s: node(s, "a")["style"].update(fill="#ff0000"), move_a,
    lambda s: node(s, "p")["geometry"].update(d="M 5 40 L 25 40 L 25 55 Z"),
    add_user, lambda s: s["objects"].remove(node(s, "a")),
    lambda s: s["resources"][0].update(stops=[[0, "#00ff00"], [1, "#ffff00"]]),
    user_order, lambda s: node(s, "a").update(parent="h"),
])
def test_user_only_edits_survive_candidate_update(project, tmp_path, change):
    current = edit(project, change)
    candidate = pure_scene()
    node(candidate, "stable")["geometry"]["rx"] = 12
    before = snapshot(project)
    status, out = rerun(tmp_path, project, candidate)
    assert status["status"] == "ready"
    assert snapshot(project) == before
    merged = load_project(out/"merged")
    expected = copy.deepcopy(current)
    node(expected, "stable")["geometry"]["rx"] = 12
    for key in ("objects", "resources", "canvas"):
        if key == "objects":
            assert {o["id"]: o for o in merged[key]} == {o["id"]: o for o in expected[key]}
            for parent in (None, "g", "h"):
                assert [o["id"] for o in merged[key] if o.get("parent") == parent] == [o["id"] for o in expected[key] if o.get("parent") == parent]
        else:
            assert merged[key] == expected[key]
    accept_generation(project, out, base_revision=current["revision"])
    accepted = load_project(project)
    assert accepted == merged
    scope = project_extras(project)["generation.json"]["input"]["scope"]
    assert "user" not in scope["objects"] and "user_shade" not in scope["resources"]


def test_candidate_only_add_delete_resource_canvas_and_order(project, tmp_path):
    candidate = pure_scene()
    candidate["objects"].remove(node(candidate, "p"))
    node(candidate, "a")["style"]["fill"] = "url(#new)"
    candidate["resources"].append(gradient("new"))
    node(candidate, "b")["style"]["fill"] = "#111111"
    candidate["resources"].remove(candidate["resources"][0])
    candidate["objects"].append({"id": "new_object", "kind": "rect", "geometry": {"x": 1, "y": 1, "width": 2, "height": 2}})
    candidate["canvas"]["background"] = "#eeeeee"
    user_order(candidate)
    status, out = rerun(tmp_path, project, candidate)
    assert status["status"] == "ready"
    accept_generation(project, out, base_revision=0)
    actual = load_project(project)
    assert {o["id"]: o for o in actual["objects"]} == {o["id"]: o for o in candidate["objects"]}
    assert actual["resources"] == candidate["resources"]
    assert actual["canvas"] == candidate["canvas"]
    assert [o["id"] for o in actual["objects"] if o.get("parent") == "g"] == ["b", "a"]


def test_same_changes_and_recursive_fields_are_not_conflicts(project, tmp_path):
    def change(scene):
        node(scene, "a")["style"]["fill"] = "#ff0000"
        node(scene, "a")["geometry"]["x"] = 6
    edit(project, change)
    candidate = pure_scene()
    node(candidate, "a")["style"]["fill"] = "#ff0000"
    node(candidate, "a")["geometry"]["y"] = 8
    status, out = rerun(tmp_path, project, candidate)
    assert status["status"] == "ready"
    merged = load_project(out/"merged")
    assert node(merged, "a")["geometry"] == {"x": 6, "y": 8, "width": 20, "height": 20, "rx": 2}
    assert node(merged, "a")["style"]["fill"] == "#ff0000"


def test_exact_patch_deletes_fields_reparents_and_changes_kind(project, tmp_path):
    edit(project, lambda s: node(s, "a")["geometry"].update(x=7))
    candidate = pure_scene()
    node(candidate, "a")["style"].pop("opacity")
    node(candidate, "a")["geometry"].pop("rx")
    node(candidate, "a")["parent"] = "h"
    node(candidate, "p").update(kind="ellipse", geometry={"cx": 20, "cy": 50, "rx": 5, "ry": 5})
    status, out = rerun(tmp_path, project, candidate)
    assert status["status"] == "ready"
    accept_generation(project, out, base_revision=1)
    actual = load_project(project)
    assert node(actual, "a")["parent"] == "h"
    assert "opacity" not in node(actual, "a")["style"]
    assert "rx" not in node(actual, "a")["geometry"]
    assert node(actual, "a")["geometry"]["x"] == 7
    assert node(actual, "p")["kind"] == "ellipse"
    assert actual["revision"] == 2


@pytest.mark.parametrize("field", ["color", "matrix", "path", "array"])
def test_divergent_atomic_fields_retain_current_and_refuse_accept(project, tmp_path, field):
    def change(scene, which):
        if field == "color":
            node(scene, "a")["style"]["fill"] = "#ff0000" if which == "u" else "#00ff00"
        elif field == "matrix":
            node(scene, "a")["transform"] = [1, 0, 0, 1, 2, 0] if which == "u" else [1, 0, 0, 1, 0, 2]
        elif field == "path":
            node(scene, "p")["geometry"]["d"] = "M 5 40 L 25 40 L 25 55 Z" if which == "u" else "M 5 40 L 30 40 L 30 55 Z"
        else:
            scene["resources"][0]["stops"] = [[0, "#00ff00"], [1, "#ffff00"]] if which == "u" else [[0, "#ffffff"], [1, "#111111"]]
    current = edit(project, lambda s: change(s, "u"))
    candidate = pure_scene()
    change(candidate, "c")
    before = snapshot(project)
    status, out = rerun(tmp_path, project, candidate)
    assert status["status"] == "conflicts"
    merged = load_project(out/"merged")
    assert merged["objects"] == current["objects"] or {o["id"]: o for o in merged["objects"]} == {o["id"]: o for o in current["objects"]}
    assert merged["resources"] == current["resources"]
    assert any(f["reason"] == "divergent_field" for f in load_json(out/"conflicts.json"))
    with pytest.raises(ValueError, match="unresolved conflicts"):
        accept_generation(project, out, base_revision=1)
    assert snapshot(project) == before


@pytest.mark.parametrize("case", ["collision", "cross_collision", "candidate_delete", "user_delete"])
def test_presence_collision_and_delete_modification_conflicts(project, tmp_path, case):
    candidate = pure_scene()
    if case == "collision":
        edit(project, add_user)
        add_user(candidate)
    elif case == "cross_collision":
        edit(project, add_user)
        candidate["resources"].append(gradient("user"))
    elif case == "candidate_delete":
        edit(project, move_a)
        candidate["objects"].remove(node(candidate, "a"))
    else:
        edit(project, lambda s: s["objects"].remove(node(s, "a")))
        node(candidate, "a")["style"]["fill"] = "#00ff00"
    before = snapshot(project)
    status, out = rerun(tmp_path, project, candidate)
    assert status["status"] == "conflicts"
    reasons = {f["reason"] for f in load_json(out/"conflicts.json")}
    assert ("id_collision" if "collision" in case else "delete_change") in reasons
    with pytest.raises(ValueError, match="unresolved conflicts"):
        accept_generation(project, out, base_revision=1)
    assert snapshot(project) == before


def test_dangling_typed_reference_from_merge_is_conflict(project, tmp_path):
    def change(scene):
        node(scene, "b")["semantic"] = {"peer": {"type": "object", "id": "a"}}
    edit(project, change)
    candidate = pure_scene()
    candidate["objects"].remove(node(candidate, "a"))
    before = snapshot(project)
    status, out = rerun(tmp_path, project, candidate)
    assert status["status"] == "conflicts"
    assert load_json(out/"report.json")["preview_fallback_to_current"]
    assert "a" not in {o["id"] for o in load_json(out/"proposed-scene.json")["objects"]}
    with pytest.raises(ValueError, match="unresolved conflicts"):
        accept_generation(project, out, base_revision=1)
    assert snapshot(project) == before


def test_sibling_orders_diverge_as_atomic_order(project, tmp_path):
    edit(project, user_order)  # b,a,p
    candidate = pure_scene()
    a, p = candidate["objects"].index(node(candidate, "a")), candidate["objects"].index(node(candidate, "p"))
    candidate["objects"][a], candidate["objects"][p] = candidate["objects"][p], candidate["objects"][a]
    status, out = rerun(tmp_path, project, candidate)
    assert status["status"] == "conflicts"
    assert any(f["path"] == ["order", "g"] for f in load_json(out/"conflicts.json"))
    assert [o["id"] for o in load_project(out/"merged")["objects"] if o.get("parent") == "g"] == ["b", "a", "p"]


def test_independent_insertions_keep_both_owners(project, tmp_path):
    edit(project, add_user)
    candidate = pure_scene()
    candidate["objects"].insert(candidate["objects"].index(node(candidate, "b")),
                                 {"id": "new", "parent": "g", "kind": "rect", "geometry": {"x": 2, "y": 2, "width": 2, "height": 2}})
    status, out = rerun(tmp_path, project, candidate)
    assert status["status"] == "ready"
    assert [o["id"] for o in load_project(out/"merged")["objects"] if o.get("parent") == "g"] == ["a", "new", "b", "p", "user"]


def test_pure_baseline_is_C_across_second_rerun(project, tmp_path):
    def user(scene):
        node(scene, "a")["style"]["fill"] = "#ff0000"
        scene["objects"].remove(node(scene, "p"))
        add_user(scene)
    edit(project, user)
    candidate = pure_scene()
    node(candidate, "stable")["geometry"]["rx"] = 12
    status, out = rerun(tmp_path, project, candidate, "first")
    assert status["status"] == "ready"
    accept_generation(project, out, base_revision=1)
    baseline = project_extras(project)["generation.json"]["baseline"]
    assert node(baseline, "a")["style"]["fill"] == "#000000"
    assert "p" in {o["id"] for o in baseline["objects"]}
    assert "user" not in {o["id"] for o in baseline["objects"]}
    node(candidate, "stable")["geometry"]["rx"] = 14
    status, out = rerun(tmp_path, project, candidate, "second")
    assert status["status"] == "ready"
    accept_generation(project, out, base_revision=2)
    actual = load_project(project)
    assert node(actual, "a")["style"]["fill"] == "#ff0000"
    assert "p" not in {o["id"] for o in actual["objects"]}
    assert node(actual, "stable")["geometry"]["rx"] == 14
    assert node(actual, "user")["style"]["fill"] == "url(#user_shade)"


def rehash_project_file(project, name):
    receipt = load_json(project/"receipt.json")
    receipt["artifacts"][name] = {"sha256": digest(project/name), "bytes": (project/name).stat().st_size}
    save(project/"receipt.json", receipt)


@pytest.mark.parametrize("same_revision", [False, True])
def test_current_stale_after_diff_including_same_revision_content(project, tmp_path, same_revision):
    candidate = pure_scene()
    node(candidate, "stable")["geometry"]["rx"] = 12
    _, out = rerun(tmp_path, project, candidate)
    if same_revision:
        changed = load_project(project)
        node(changed, "a")["style"]["fill"] = "#ff0000"
        save(project/"scene.json", changed)
        rehash_project_file(project, "scene.json")
    else:
        edit(project, move_a)
    before = snapshot(project)
    with pytest.raises(ValueError, match="stale current project"):
        accept_generation(project, out, base_revision=0)
    assert snapshot(project) == before


def test_writer_rechecks_same_revision_canonical_state_under_lock(project):
    current, extras = load_project(project), project_extras(project)
    bound = project_state_sha256(current, extras)
    replacement = copy.deepcopy(current)
    replacement["revision"] += 1
    modified = copy.deepcopy(current)
    node(modified, "a")["geometry"]["x"] = 9
    save(project/"scene.json", modified)
    rehash_project_file(project, "scene.json")
    before = snapshot(project)
    with pytest.raises(ValueError, match="stale current project"):
        write_project(replacement, project, expected_revision=0, expected_state_sha256=bound)
    assert snapshot(project) == before


@pytest.mark.parametrize("path", ["source/scene.json", "source/generation-input.json", "merged/scene.json",
                                  "merged/preview.png", "merged/generation.json", "report.json", "conflicts.json"])
def test_all_diff_payload_hashes_are_checked(project, tmp_path, path):
    _, out = rerun(tmp_path, project, pure_scene())
    with (out/path).open("ab") as handle:
        handle.write(b" ")
    before = snapshot(project)
    with pytest.raises(ValueError, match="hash/size mismatch"):
        accept_generation(project, out, base_revision=0)
    assert snapshot(project) == before


def test_original_source_mutation_rejected_even_if_semantically_equal(project, tmp_path):
    _, out = rerun(tmp_path, project, pure_scene())
    (tmp_path/"source-diff"/"generation-input.json").write_text(
        json.dumps(load_json(tmp_path/"source-diff"/"generation-input.json")), encoding="utf-8")
    before = snapshot(project)
    with pytest.raises(ValueError, match="source candidate changed"):
        accept_generation(project, out, base_revision=0)
    assert snapshot(project) == before


def test_rehashed_merged_payload_cannot_change_recomputed_scene(project, tmp_path):
    _, out = rerun(tmp_path, project, pure_scene())
    changed = load_project(out/"merged")
    node(changed, "a")["style"]["fill"] = "#ff0000"
    save(out/"merged"/"scene.json", changed)
    rehash_project_file(out/"merged", "scene.json")
    manifest = load_json(out/"candidate.json")
    for name in manifest["artifacts"]:
        manifest["artifacts"][name] = {"sha256": digest(out/name), "bytes": (out/name).stat().st_size}
    save(out/"candidate.json", manifest)
    before = snapshot(project)
    with pytest.raises(ValueError, match="SVG does not match|differs from recomputation"):
        accept_generation(project, out, base_revision=0)
    assert snapshot(project) == before


def deps(*pairs):
    return [{"object_id": child, "depends_on": [{"type": "object", "id": parent}]} for child, parent in pairs]


@pytest.mark.parametrize("change", [move_a, lambda s: node(s, "g").update(transform=[1, 0, 0, 1, 4, 0]),
                                    lambda s: node(s, "a")["geometry"].update(width=22)])
def test_stale_baked_dependency_rejects_field_disjoint_geometry(tmp_path, change):
    baseline = pure_scene()
    graph = deps(("p", "a"), ("stable", "p"))
    attached, extras = attach_generation(baseline, record(baseline, dependencies=graph))
    project = tmp_path/"project"
    write_project(attached, project, scale=1, extras=extras)
    edit(project, change)
    candidate = pure_scene()
    node(candidate, "stable")["geometry"]["rx"] = 12
    # Even omitting the old declaration cannot bypass stale dependencies.
    before = snapshot(project)
    status, out = rerun(tmp_path, project, candidate)
    assert status["status"] == "conflicts"
    assert any(f["reason"] == "stale_baked_geometry" and f["path"][1] == "stable" for f in load_json(out/"conflicts.json"))
    with pytest.raises(ValueError, match="unresolved conflicts"):
        accept_generation(project, out, base_revision=1)
    assert snapshot(project) == before


def test_dependency_not_conflict_when_both_branches_have_identical_operand(tmp_path):
    baseline = pure_scene()
    graph = deps(("p", "a"))
    attached, extras = attach_generation(baseline, record(baseline, dependencies=graph))
    project = tmp_path/"project"
    write_project(attached, project, scale=1, extras=extras)
    edit(project, move_a)
    candidate = pure_scene()
    move_a(candidate)
    node(candidate, "p")["geometry"]["d"] = "M 5 40 L 25 40 L 25 55 Z"
    status, _ = rerun(tmp_path, project, candidate, input_record=record(candidate, dependencies=graph))
    assert status["status"] == "ready"


@pytest.mark.parametrize("graph", [deps(("p", "a"), ("a", "p")), deps(("p", "missing")),
                                    deps(("p", "p")), deps(("p", "a"), ("p", "b")),
                                    [{"object_id": "p", "depends_on": [{"type": "file", "id": "a"}]}]])
def test_graph_is_strict_finite_DAG(graph):
    with pytest.raises(ValueError):
        record(pure_scene(), dependencies=graph)


def test_resource_dependency_fingerprint(tmp_path):
    baseline = pure_scene()
    graph = [{"object_id": "p", "depends_on": [{"type": "resource", "id": "shade"}]}]
    attached, extras = attach_generation(baseline, record(baseline, dependencies=graph))
    project = tmp_path/"project"
    write_project(attached, project, scale=1, extras=extras)
    edit(project, lambda s: s["resources"][0].update(end=[0, 1]))
    candidate = pure_scene()
    node(candidate, "p")["geometry"]["d"] = "M 5 40 L 25 40 L 25 55 Z"
    status, out = rerun(tmp_path, project, candidate, input_record=record(candidate, dependencies=graph))
    assert status["status"] == "conflicts"
    assert any(f["reason"] == "stale_baked_geometry" for f in load_json(out/"conflicts.json"))


def test_invalid_source_and_render_failures_leave_project_atomic(project, tmp_path, monkeypatch):
    bad = pure_scene()
    node(bad, "a")["geometry"]["width"] = -1
    root = tmp_path/"bad"
    root.mkdir()
    save(root/"scene.json", bad)
    save(root/"generation-input.json", record(pure_scene()))
    before = snapshot(project)
    with pytest.raises(ValueError):
        diff_generation(project, root, tmp_path/"bad-diff")
    assert snapshot(project) == before and not (tmp_path/"bad-diff").exists()
    _, out = rerun(tmp_path, project, pure_scene())
    import scientific_figure.project as persistence
    def fail(*args, **kwargs):
        raise OSError("injected render failure")
    monkeypatch.setattr(persistence, "render_svg", fail)
    with pytest.raises(OSError, match="injected render failure"):
        accept_generation(project, out, base_revision=0)
    assert snapshot(project) == before
    assert not list(tmp_path.glob(".project.stage-*"))


def test_commit_rename_failure_rolls_back_project(project, tmp_path, monkeypatch):
    candidate = pure_scene()
    node(candidate, "stable")["geometry"]["rx"] = 12
    _, out = rerun(tmp_path, project, candidate)
    before = snapshot(project)
    from pathlib import Path
    real = Path.rename
    def fail_stage(self, target):
        if self.name.startswith(".project.stage-"):
            raise OSError("injected rename failure")
        return real(self, target)
    monkeypatch.setattr(Path, "rename", fail_stage)
    with pytest.raises(OSError, match="injected rename failure"):
        accept_generation(project, out, base_revision=0)
    assert snapshot(project) == before


def test_copy_bundle_and_reload_keep_inert_source_and_closure(tmp_path):
    baseline = pure_scene()
    sentinel = tmp_path/"executed"
    source_text = f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('BAD')\n"
    rec = generation_input(baseline, language="python", source=source_text, params={"label": "x"},
                           backend={"name": "host", "version": "1"}, inputs={"license": "host"})
    attached, extras = attach_generation(baseline, rec)
    project = tmp_path/"project"
    write_project(attached, project, scale=1, extras=extras)
    clone = tmp_path/"clone"
    write_project(load_project(project), clone, scale=1, extras=project_extras(project))
    assert project_extras(clone)["generation.json"] == extras["generation.json"]
    plan = save(tmp_path/"bundle-plan.json", {"schema_version": "0.1", "assets": [{"id": "asset", "project": "clone"}]})
    create_bundle(plan, tmp_path/"bundle")
    shutil.rmtree(project)
    shutil.rmtree(clone)
    validate_bundle(tmp_path/"bundle")
    member = tmp_path/"bundle"/"assets"/"asset"
    assert load_project(member) == attached
    assert project_extras(member)["generation.json"]["input"]["source"]["text"] == source_text
    assert not sentinel.exists()
    # An internally rehashed bundle cannot hide a broken generation binding.
    altered = project_extras(member)["generation.json"]
    altered["input"]["params"]["label"] = "tampered"
    with pytest.raises(ValueError, match="binding mismatch"):
        validate_generation(attached, {"generation.json": altered})
    save(member/"generation.json", altered)
    rehash_project_file(member, "generation.json")
    with pytest.raises(ValueError, match="binding mismatch"):
        load_project(member)
    root_receipt = load_json(tmp_path/"bundle"/"receipt.json")
    for name in root_receipt["artifacts"]:
        path = tmp_path/"bundle"/name
        root_receipt["artifacts"][name] = {"sha256": digest(path), "bytes": path.stat().st_size}
    save(tmp_path/"bundle"/"receipt.json", root_receipt)
    with pytest.raises(ValueError, match="binding mismatch"):
        validate_bundle(tmp_path/"bundle")
    assert not sentinel.exists()


def test_null_generation_documents_and_bindings_fail_closed():
    scene = pure_scene()
    with pytest.raises(ValueError, match="snapshot fields"):
        validate_generation(scene, {"generation.json": None})
    scene["provenance"] = {"generation": None}
    with pytest.raises(ValueError, match="missing generation.json"):
        validate_generation(scene, {})


def test_generation_full_closure_and_binding_required(project, tmp_path):
    scene, extras = load_project(project), project_extras(project)
    with pytest.raises(ValueError, match="missing generation.json"):
        write_project(scene, tmp_path/"missing")
    altered = copy.deepcopy(extras)
    altered["generation.json"]["baseline"]["objects"].remove(node(altered["generation.json"]["baseline"], "a"))
    with pytest.raises(ValueError, match="baseline/hash"):
        write_project(scene, tmp_path/"altered", extras=altered)
    scene["provenance"].pop("generation")
    with pytest.raises(ValueError, match="binding mismatch"):
        write_project(scene, tmp_path/"unbound", extras=extras)


@pytest.mark.parametrize("mutation", [
    lambda s: node(s, "a").update(semantic={"object_id": "missing"}),
    lambda s: node(s, "a").update(semantic={"type": "resource", "id": "missing"}),
    lambda s: node(s, "a").update(semantic={"type": "unknown_ref", "id": "a"}),
    lambda s: s.update(provenance={"context": {"unsupported": True}}),
])
def test_candidate_unsupported_provenance_and_typed_refs_rejected(mutation):
    candidate = pure_scene()
    mutation(candidate)
    with pytest.raises(ValueError):
        record(candidate)


def test_initial_subset_never_claims_user_objects(tmp_path):
    pure = pure_scene()
    full = pure_scene()
    add_user(full)
    scene, extras = attach_generation(full, record(pure), baseline=pure)
    write_project(scene, tmp_path/"project", scale=1, extras=extras)
    assert "user" not in extras["generation.json"]["input"]["scope"]["objects"]
    wrong = pure_scene()
    move_a(wrong)
    with pytest.raises(ValueError, match="differs from project"):
        attach_generation(full, record(wrong), baseline=wrong)


def test_context_closure_is_preserved_and_checked_on_generation(tmp_path):
    from scientific_figure.context import apply_context
    pure = pure_scene()
    context = save(tmp_path/"context-input.json", {"schema_version": "0.1", "id": "intent", "preserved_ids": ["a"]})
    scene, extras = apply_context(pure, context)
    scene, generation = attach_generation(scene, record(pure), baseline=pure)
    extras.update(generation)
    project = tmp_path/"project"
    write_project(scene, project, scale=1, extras=extras)
    candidate = pure_scene()
    node(candidate, "stable")["geometry"]["rx"] = 12
    status, out = rerun(tmp_path, project, candidate, "valid")
    assert status["status"] == "ready"
    accept_generation(project, out, base_revision=0)
    assert project_extras(project)["context.json"] == extras["context.json"]
    candidate["objects"].remove(node(candidate, "a"))
    status, out = rerun(tmp_path, project, candidate, "invalid")
    assert status["status"] == "conflicts"
    assert any(f["reason"] == "invalid_merged_closure" for f in load_json(out/"conflicts.json"))


def test_dependent_user_color_does_not_change_sibling_operand_fingerprint(tmp_path):
    baseline = pure_scene()
    graph = deps(("p", "a"))
    attached, extras = attach_generation(baseline, record(baseline, dependencies=graph))
    project = tmp_path/"project"
    write_project(attached, project, scale=1, extras=extras)
    edit(project, lambda s: node(s, "p").update(style={"fill": "#ff0000"}))
    candidate = pure_scene()
    node(candidate, "p")["geometry"]["d"] = "M 5 40 L 25 40 L 25 55 Z"
    status, out = rerun(tmp_path, project, candidate, input_record=record(candidate, dependencies=graph))
    assert status["status"] == "ready"
    assert node(load_project(out/"merged"), "p")["style"]["fill"] == "#ff0000"


def test_surviving_dependency_declarations_cannot_be_silently_dropped(tmp_path):
    baseline = pure_scene()
    attached, extras = attach_generation(baseline, record(baseline, dependencies=deps(("p", "a"))))
    project = tmp_path/"project"
    write_project(attached, project, scale=1, extras=extras)
    status, out = rerun(tmp_path, project, pure_scene())
    assert status["status"] == "conflicts"
    assert any(f["reason"] == "dependency_declaration_removed" for f in load_json(out/"conflicts.json"))
    with pytest.raises(ValueError, match="unresolved conflicts"):
        accept_generation(project, out, base_revision=0)


def effects_scene():
    scene = pure_scene()
    scene["schema_version"] = "0.2"
    scene["resources"].extend([
        {"id": "clip", "kind": "clip_path", "d": "M0 0H90V70H0Z"},
        {"id": "mask", "kind": "alpha_mask", "region": [0, 0, 100, 80],
         "shapes": [{"d": "M0 0H100V80H0Z", "alpha": 0.5}]}])
    node(scene, "a")["clip"] = "clip"
    node(scene, "b")["mask"] = "mask"
    scene["objects"].extend([
        {"id": "over", "kind": "path", "geometry": {"d": "M10 30H70V40H10Z"}},
        {"id": "under", "kind": "path", "geometry": {"d": "M35 10H45V70H35Z"}}])
    scene["occlusions"] = [{"id": "cross", "over": "over", "under": "under", "frame": None,
                            "region": [20, 20, 40, 40], "gap": 1}]
    return scene


@pytest.fixture
def effects_project(tmp_path):
    scene = effects_scene()
    attached, extras = attach_generation(scene, record(scene))
    project = tmp_path/"project"
    write_project(attached, project, scale=1, extras=extras)
    return project


def test_scene02_generation_preserves_user_effect_resources_and_live_geometry(effects_project, tmp_path):
    project = effects_project
    def user(scene):
        node(scene, "over")["transform"] = [1, 0, 0, 1, 2, 0]
        next(r for r in scene["resources"] if r["id"] == "clip")["d"] = "M0 0H80V60H0Z"
        next(r for r in scene["resources"] if r["id"] == "mask")["shapes"][0]["alpha"] = 0.7
    current = edit(project, user)
    candidate = effects_scene()
    node(candidate, "stable")["geometry"]["rx"] = 12
    candidate["occlusions"][0]["gap"] = 2
    status, out = rerun(tmp_path, project, candidate)
    assert status["status"] == "ready"
    accept_generation(project, out, base_revision=1)
    actual = load_project(project)
    assert actual["schema_version"] == "0.2"
    assert node(actual, "over")["transform"] == node(current, "over")["transform"]
    assert {r["id"]: r for r in actual["resources"]} == {r["id"]: r for r in current["resources"]}
    assert actual["occlusions"][0]["gap"] == 2
    assert node(actual, "a")["clip"] == "clip" and node(actual, "b")["mask"] == "mask"
    pure = project_extras(project)["generation.json"]["baseline"]
    assert pure["schema_version"] == "0.2" and pure["occlusions"] == candidate["occlusions"]
    assert "transform" not in node(pure, "over")
    assert "effect_evaluation" in load_json(project/"qa.json")
    assert "paths" not in actual and "paths" not in pure
    bundle_plan = save(tmp_path/"bundle-plan.json", {"schema_version": "0.1", "assets": [{"id": "asset", "project": "project"}]})
    create_bundle(bundle_plan, tmp_path/"bundle")
    validate_bundle(tmp_path/"bundle")
    assert project_extras(tmp_path/"bundle"/"assets"/"asset")["generation.json"]["baseline"] == pure


def test_scene02_relations_atomic_conflict_retains_user(effects_project, tmp_path):
    edit(effects_project, lambda s: s["occlusions"][0].update(gap=2))
    candidate = effects_scene()
    candidate["occlusions"][0]["gap"] = 3
    status, out = rerun(tmp_path, effects_project, candidate)
    assert status["status"] == "conflicts"
    assert load_project(out/"merged")["occlusions"][0]["gap"] == 2
    assert any(f["path"] == ["occlusions"] for f in load_json(out/"conflicts.json"))
    with pytest.raises(ValueError, match="unresolved conflicts"):
        accept_generation(effects_project, out, base_revision=1)


def test_scene02_user_new_relation_is_not_claimed_on_accept(effects_project, tmp_path):
    def user(scene):
        scene["occlusions"][0]["id"] = "user_cross"
    edit(effects_project, user)
    candidate = effects_scene()
    node(candidate, "stable")["geometry"]["rx"] = 12
    status, out = rerun(tmp_path, effects_project, candidate, "first")
    assert status["status"] == "ready"
    accept_generation(effects_project, out, base_revision=1)
    assert load_project(effects_project)["occlusions"][0]["id"] == "user_cross"
    assert project_extras(effects_project)["generation.json"]["baseline"]["occlusions"][0]["id"] == "cross"
    # Candidate cannot acquire the new user relation ID, even with identical data.
    candidate["occlusions"][0]["id"] = "user_cross"
    status, out = rerun(tmp_path, effects_project, candidate, "second")
    assert status["status"] == "conflicts"
    assert any(f["reason"] == "id_collision" and f["path"][-1] == "user_cross" for f in load_json(out/"conflicts.json"))


def test_scene02_candidate_can_clear_effect_fields_and_relations(effects_project, tmp_path):
    candidate = effects_scene()
    candidate.pop("occlusions")
    node(candidate, "a").pop("clip")
    node(candidate, "b").pop("mask")
    candidate["resources"] = [r for r in candidate["resources"] if r["kind"] == "linear_gradient"]
    status, out = rerun(tmp_path, effects_project, candidate)
    assert status["status"] == "ready"
    accept_generation(effects_project, out, base_revision=0)
    actual = load_project(effects_project)
    assert actual["schema_version"] == "0.2" and "occlusions" not in actual
    assert "clip" not in node(actual, "a") and "mask" not in node(actual, "b")
    assert actual["resources"] == candidate["resources"]


def test_scene_version_upgrade_uses_same_exact_transaction(project, tmp_path):
    candidate = effects_scene()
    status, out = rerun(tmp_path, project, candidate)
    assert status["status"] == "ready"
    accept_generation(project, out, base_revision=0)
    assert load_project(project)["schema_version"] == "0.2"
    assert load_project(project)["occlusions"] == candidate["occlusions"]


def test_writer_retains_semantic_effect_needs_review_without_host_approval(tmp_path):
    from scientific_figure.reconstruct import semantic_audit
    scene = effects_scene()
    scene["provenance"] = {"mode": "semantic", "brief": {"protected_ids": ["a"]}}
    audit = semantic_audit(scene)
    assert audit["status"] == "needs_review"
    project = tmp_path/"semantic-project"
    result = write_project(scene, project, scale=1)
    assert result["quality_status"] == "needs_review"
    qa = load_json(project/"qa.json")
    assert qa["semantic_audit"] == audit
    assert "drawing_review" in qa and "effect_evaluation" in qa
    review = load_json(project/"review.json")
    assert review["mechanical"]["semantic"]["status"] == "needs_review"
    assert review["visual"]["status"] == "not_executed"
    assert review["scientific"]["status"] == "not_executed"
    assert load_project(project) == scene


def test_writer_still_rejects_empty_protected_effect_coverage(tmp_path):
    scene = effects_scene()
    scene["provenance"] = {"mode": "semantic", "brief": {"protected_ids": ["a"]}}
    next(r for r in scene["resources"] if r["id"] == "clip")["d"] = "M90 70H95V75H90Z"
    with pytest.raises(ValueError, match="semantic mechanical audit rejected"):
        write_project(scene, tmp_path/"rejected", scale=1)
    assert not (tmp_path/"rejected").exists()


def test_writer_rejects_unknown_semantic_audit_status(tmp_path, monkeypatch):
    import importlib
    reconstruction = importlib.import_module("scientific_figure.reconstruct")
    monkeypatch.setattr(reconstruction, "semantic_audit", lambda scene: {"status": "unknown", "reasons": ["unknown audit state"]})
    scene = pure_scene()
    scene["provenance"] = {"mode": "semantic"}
    with pytest.raises(ValueError, match="semantic mechanical audit rejected"):
        write_project(scene, tmp_path/"unknown", scale=1)
    assert not (tmp_path/"unknown").exists()


def test_CLI_diff_accept_smoke(project, tmp_path):
    candidate = pure_scene()
    node(candidate, "stable")["geometry"]["rx"] = 12
    src = source(tmp_path, candidate)
    out = tmp_path/"diff"
    def cli(*args, expected=0):
        result = subprocess.run([sys.executable, "-I", "-m", "scientific_figure.cli", *map(str, args)],
                                cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == expected, (result.stdout, result.stderr)
        return json.loads(result.stdout)
    assert cli("generation", "diff", project, "--candidate", src, "--output", out)["status"] == "ready"
    assert cli("generation", "accept", project, "--candidate", out, "--base-revision", 0)["revision"] == 1
    assert node(load_project(project), "stable")["geometry"]["rx"] == 12
    assert cli("generation", "accept", project, "--candidate", out, "--base-revision", 0, expected=2)["status"] == "failed"

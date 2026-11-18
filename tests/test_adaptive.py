"""Contracts for source comparison, real selection, structural edits and repair.

Synthetic cases exercise failure mechanics, not cross-domain generalization.
"""
import copy
import hashlib
import json
import subprocess
import sys

import pytest
from PIL import Image, ImageDraw

from scientific_figure.analysis import analyze
from scientific_figure.candidate_search import SearchBudget, search
from scientific_figure.editing import apply_patch, export_scene
from scientific_figure.evaluation import evaluate, render_pixels
from scientific_figure.project import load_project, write_project
from scientific_figure.reconstruct import reconstruct
from scientific_figure.repair import repair_scene
from scientific_figure.strategies import build_candidate


def source(tmp_path, internal=False, hole=False):
    image = Image.new("RGB", (128, 112), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((10, 10, 116, 100), fill="#229966")
    if internal: draw.ellipse((46, 38, 78, 70), fill="#2244cc")
    if hole: draw.ellipse((46, 38, 78, 70), fill="white")
    path = tmp_path/"source.png"
    image.save(path)
    return path


def plan(path, methods=None, parts=None):
    return {"schema_version": "0.2",
            "source": {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": [128,112], "region": [0,0,128,112]},
            "canvas": {"width":128,"height":112,"background":"#ffffff"},
            "steps": [{"step_id":"material","operation":"auto_reconstruct","object":{"id":"material","kind":"group"},
                       "region":[0,0,128,112],"parameters":{"colors":2,"background":"#ffffff",
                       **({"methods":methods} if methods else {}), **({"parts":parts} if parts else {})}}]}


def test_source_comparison_rejects_internal_omission_and_selects_layers(tmp_path):
    path = source(tmp_path, internal=True)
    evidence = analyze(path, colors=2)
    scene, report = search(path, evidence)
    records = {r["method"]:r for r in report["records"]}
    assert "internal_region_missing_or_merged" in records["flat_contour"]["audit"]["reasons"]
    assert report["selection"]["candidate_id"] in {"color_layers", "region_bands"}
    assert len(scene["objects"]) > len(records["primitive_fit"]["scene"]["objects"])
    assert records["color_layers"]["audit"]["status"] == "passed"
    assert records["color_layers"]["audit"]["scientific_review"] == "host_required"


def test_simple_geometry_can_win_without_shape_name(tmp_path):
    path = source(tmp_path)
    _, report = search(path, analyze(path, colors=2))
    assert report["selection"]["candidate_id"] == "primitive_fit"
    assert report["records"][0]["scene"]["objects"][1]["kind"] == "ellipse"


def test_hole_is_protected_against_primitive_fit(tmp_path):
    path = source(tmp_path, hole=True)
    _, report = search(path, analyze(path, colors=2))
    primitive = report["records"][0]
    assert "visible_topology_changed" in primitive["audit"]["reasons"]
    assert report["selection"]["candidate_id"] != "primitive_fit"


def test_variable_path_counts_compare_equivalent_composition(tmp_path):
    image = Image.new("RGB", (32,32), "white")
    ImageDraw.Draw(image).rectangle((4,4,27,27), fill="#229966")
    path = tmp_path/"rect.png";image.save(path)
    evidence = analyze(path)
    one = {"schema_version":"0.1","revision":0,"canvas":{"width":32,"height":32},"objects":[
        {"id":"one","kind":"rect","geometry":{"x":4,"y":4,"width":24,"height":24},"style":{"fill":"#229966"}}]}
    two = copy.deepcopy(one)
    two["objects"][0]["geometry"]["width"] = 12
    right = copy.deepcopy(two["objects"][0]);right["id"]="right";right["geometry"]["x"]=16
    two["objects"].append(right)
    a,b = evaluate(path,evidence,one),evaluate(path,evidence,two)
    assert a["F"] == b["F"]
    assert a["status"] == b["status"] == "passed"
    assert a["C"]["draw_nodes"] == 1 and b["C"]["draw_nodes"] == 2


def test_analysis_hash_and_shared_budget_reject_invalid_reuse(tmp_path):
    path=source(tmp_path)
    evidence=analyze(path)
    _,report=search(path,evidence,budget=SearchBudget({"max_candidates":1,"max_renders":1}))
    assert report["budget"]["render_count"] == 1
    assert sum(r["status"]=="not_executed" for r in report["records"]) == 2
    Image.new("RGB",(128,112),"red").save(path)
    with pytest.raises(ValueError,match="hash"):
        search(path,evidence)


def test_required_part_is_not_satisfied_by_named_outer_group(tmp_path):
    path=source(tmp_path,internal=True)
    evidence=analyze(path,colors=2,parts=[{"id":"inside","color":"#2244cc"}])
    _,report=search(path,evidence)
    assert report["selection"]["candidate_id"] in {"color_layers", "region_bands"}
    assert "required_part_not_addressable" in report["records"][0]["audit"]["reasons"]


def test_atomic_replacement_migrates_links_preserves_unrelated_and_rolls_back(tmp_path):
    path=source(tmp_path)
    scene=reconstruct(path,plan(path))
    scene["objects"].extend([
        {"id":"fixed","kind":"rect","geometry":{"x":0,"y":0,"width":3,"height":3}},
        {"id":"link","kind":"connector","geometry":{"from":"material_body","to":"fixed"},"style":{"stroke":"#000000"}}])
    replacement=[{"id":"material","kind":"group"},
                 {"id":"new_body","kind":"ellipse","parent":"material","geometry":{"cx":64,"cy":55,"rx":53,"ry":45}}]
    patch={"schema_version":"0.2","base_revision":0,"operations":[
        {"op":"replace_subtree","id":"material","objects":replacement}]}
    snapshot=copy.deepcopy(scene)
    with pytest.raises(ValueError,match="migration"):
        apply_patch(scene,patch)
    assert scene==snapshot
    patch["operations"][0]["id_map"]={"material_body":"new_body"}
    changed=apply_patch(scene,patch)
    assert changed["revision"]==1
    assert next(o for o in changed["objects"] if o["id"]=="fixed")==scene["objects"][-2]
    assert changed["objects"][-1]["geometry"]["from"]=="new_body"
    with pytest.raises(ValueError,match="revision"):
        apply_patch(changed,patch)


def test_directed_repair_adds_real_internal_layer_and_checks_context(tmp_path):
    path=source(tmp_path,internal=True)
    scene=reconstruct(path,plan(path,methods=["flat_contour"]))
    fixed,report=repair_scene(scene,path,"material",{"action":"restore_internal_regions",
        "parts":[{"id":"inside","color":"#2244cc","role":"host_internal"}]})
    assert report["status"]=="repaired"
    assert fixed["revision"]==1 and scene["revision"]==0
    assert report["after"]["F"]["source_color_mae"] < report["before"]["F"]["source_color_mae"]
    layer=next(o for o in fixed["objects"] if o.get("semantic",{}).get("part_id")=="inside")
    selected=export_scene(fixed,[layer["id"]])
    assert selected["objects"]
    changed=apply_patch(fixed,{"base_revision":1,"operations":[{"id":layer["id"],"set":{"transform":[1,0,0,1,4,0]}}]})
    assert render_pixels(changed).tolist()!=render_pixels(fixed).tolist()
    recolored=apply_patch(fixed,{"base_revision":1,"operations":[{"id":layer["id"],"set":{"style":{"fill":"#ff8800"}}}]})
    assert render_pixels(recolored).tolist()!=render_pixels(fixed).tolist()
    assert [o for o in recolored["objects"] if o["id"]!=layer["id"]]==[o for o in fixed["objects"] if o["id"]!=layer["id"]]


def test_structural_add_reorder_remove_is_one_transaction(tmp_path):
    path=source(tmp_path)
    scene=reconstruct(path,plan(path))
    changed=apply_patch(scene,{"schema_version":"0.2","base_revision":0,"operations":[
        {"op":"add_subtree","objects":[{"id":"extra","kind":"rect","geometry":{"x":1,"y":1,"width":3,"height":3}}]},
        {"op":"reorder","parent":None,"order":["extra","material"]},
        {"op":"remove_subtree","id":"extra"}]})
    assert changed["revision"]==1
    assert {o["id"]:o for o in changed["objects"]}=={o["id"]:o for o in scene["objects"]}


def test_named_group_cannot_fake_independent_part_edit(tmp_path):
    path=source(tmp_path,internal=True)
    from scientific_figure.analysis import bind_analysis
    evidence=analyze(path,colors=2,parts=[{"id":"inside","color":"#2244cc"}])
    fake=build_candidate(bind_analysis(path,evidence),evidence,"material","flat_contour")
    fake["objects"][0]["semantic"]["part_id"]="inside"
    assert "part_edit_affects_wrong_region" in evaluate(path,evidence,fake)["reasons"]


def test_required_thin_detail_is_not_ignored_as_antialiasing(tmp_path):
    path=source(tmp_path)
    image=Image.open(path);ImageDraw.Draw(image).line((40,50,85,50),fill="#2244cc",width=1);image.save(path)
    evidence=analyze(path,colors=2,parts=[{"id":"thin","color":"#2244cc"}])
    _,report=search(path,evidence)
    assert report["selection"]["status"]=="selected"
    assert report["selection"]["candidate_id"]=="region_bands"
    chosen=next(r for r in report["records"] if r["candidate_id"]=="region_bands")
    assert chosen["audit"]["E"]["tasks"][0]["task_passed"]
    assert any("required_part_not_addressable" in r.get("audit",{}).get("reasons",[]) for r in report["records"])


def test_painted_hole_and_transparent_hole_share_source_compositing(tmp_path):
    image=Image.new("RGB",(32,32),"white");draw=ImageDraw.Draw(image)
    draw.rectangle((4,4,27,27),fill="#229966");draw.rectangle((12,12,19,19),fill="white")
    path=tmp_path/"hole.png";image.save(path)
    scene={"schema_version":"0.1","revision":0,"canvas":{"width":32,"height":32},"objects":[
        {"id":"body","kind":"rect","geometry":{"x":4,"y":4,"width":24,"height":24},"style":{"fill":"#229966"}},
        {"id":"hole","kind":"rect","geometry":{"x":12,"y":12,"width":8,"height":8},"style":{"fill":"#ffffff"}}]}
    assert evaluate(path,analyze(path),scene)["status"]=="passed"


def test_analysis_or_profile_change_invalidates_cached_review(tmp_path):
    from scientific_figure.evaluation import current_audit
    path=source(tmp_path)
    scene=reconstruct(path,plan(path))
    assert current_audit(scene)["status"]=="passed"
    scene["provenance"]["adaptive_context"]["analysis"]["parts"].append({"id":"missing","color":"#ff0000"})
    assert current_audit(scene)["status"]=="stale_after_edit"


def test_no_improvement_does_not_commit_revision(tmp_path):
    path=source(tmp_path)
    scene=reconstruct(path,plan(path))
    result,report=repair_scene(scene,path,"material",{"action":"reduce_fragmentation"})
    assert result==scene
    assert report["status"]=="needs_review"
    assert report["revision"]==0


def test_auto_repair_routes_from_measured_failure(tmp_path):
    path=source(tmp_path,internal=True)
    scene=reconstruct(path,plan(path,methods=["flat_contour"]))
    fixed,report=repair_scene(scene,path,"material",{"action":"auto"})
    assert report["status"]=="repaired"
    assert report["history"][0]["action"]=="restore_internal_boundaries"
    assert report["after"]["F"]["internal_boundary"]["coverage"] > report["before"]["F"]["internal_boundary"]["coverage"]
    assert fixed["revision"]==1


def test_failed_context_repair_keeps_original_untouched(tmp_path):
    path=source(tmp_path,internal=True)
    scene=reconstruct(path,plan(path,methods=["flat_contour"]))
    scene["objects"].append({"id":"unrelated","kind":"rect","geometry":{"x":0,"y":0,"width":128,"height":112},"style":{"fill":"#ff0000"}})
    snapshot=copy.deepcopy(scene)
    result,report=repair_scene(scene,path,"material",{"action":"restore_internal_regions"})
    assert result==snapshot and scene==snapshot
    assert report["status"]=="needs_review"


def test_adaptive_cli_project_receipts_and_stale_review(tmp_path):
    path=source(tmp_path,internal=True)
    file=tmp_path/"plan.json";file.write_text(json.dumps(plan(path)))
    project=tmp_path/"project"
    def cli(*args):
        process=subprocess.run([sys.executable,"-I","-m","scientific_figure.cli",*map(str,args)],cwd=tmp_path,capture_output=True,text=True)
        assert process.returncode==0,(process.stdout,process.stderr)
        return json.loads(process.stdout)
    cli("reconstruct",path,"--plan",file,"--output",project)
    loaded=load_project(project)
    assert {"analysis.json","candidates.json","review.json","overlay.png"} <= {p.name for p in project.iterdir()}
    patch=tmp_path/"patch.json";patch.write_text(json.dumps({"base_revision":0,"operations":[{"id":"material","set":{"transform":[1,0,0,1,1,0]}}]}))
    cli("edit",project,"--patch",patch)
    assert cli("review",project)["drawing_review"]["status"]=="stale_after_edit"
    assert load_project(project)["revision"]==1

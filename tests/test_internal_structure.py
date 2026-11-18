"""Internal source evidence and edit tasks, independent of scientific identity."""
import copy
import hashlib

import numpy as np
from PIL import Image, ImageDraw

from scientific_figure.analysis import analyze, bind_analysis
from scientific_figure.candidate_search import search
from scientific_figure.curves import fit_open_path
from scientific_figure.editing import apply_patch, export_scene
from scientific_figure.evaluation import evaluate, render_pixels
from scientific_figure.internal_structure import skeletonize, skeleton_chains
from scientific_figure.reconstruct import reconstruct
from scientific_figure.repair import repair_scene
from scientific_figure.strategies import build_candidate
from scientific_figure.svg import to_svg


def source(tmp_path, width=2):
    image=Image.new("RGB",(128,112),"white")
    draw=ImageDraw.Draw(image)
    draw.rectangle((8,8,119,103),fill="#b8dcc0")
    draw.arc((26,24,100,86),20,320,fill="#304838",width=width)
    draw.line((35,55,70,55,85,37),fill="#304838",width=width)
    path=tmp_path/"curves.png";image.save(path)
    return path


def plan(path, methods):
    return {"schema_version":"0.2","source":{"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
            "size":[128,112],"region":[0,0,128,112]},"canvas":{"width":128,"height":112,"background":"#ffffff"},
            "steps":[{"step_id":"asset","operation":"auto_reconstruct","object":{"id":"asset","kind":"group"},
            "region":[0,0,128,112],"parameters":{"colors":2,"background":"#ffffff","methods":methods}}]}


def test_small_color_error_cannot_hide_missing_internal_curves(tmp_path):
    path=source(tmp_path)
    evidence=analyze(path,colors=2,background="#ffffff")
    flat=build_candidate(bind_analysis(path,evidence),evidence,"asset","flat_contour")
    audit=evaluate(path,evidence,flat)
    assert audit["F"]["source_color_mae"] < 0.1
    assert audit["F"]["iou"] > 0.97
    assert "internal_boundary_missing" in audit["reasons"]
    assert evidence["internal_structure"]["scientific_identity"]=="unknown"
    actual,_=search(path,evidence,methods=["flat_contour","region_bands"])
    measured=evaluate(path,evidence,actual)
    assert measured["F"]["internal_boundary"]["coverage"] > 0.95
    assert measured["status"]=="passed"


def test_branch_graph_edges_endpoints_cycles_and_open_curve_fit():
    mask=np.zeros((31,31),bool)
    mask[15,5:26]=True;mask[5:16,15]=True
    chains,graph=skeleton_chains(skeletonize(mask))
    assert len(chains)==3 and graph["endpoints"]==3 and graph["junction_pixels"]==1
    assert sum(len(c)-1 for c in chains)==30
    points=np.array([[x,10+5*np.sin(x/10)] for x in range(40)])
    path=fit_open_path(points)
    assert "C" in path and "Z" not in path
    assert path.startswith("M0,10")
    loop=np.zeros((12,12),bool);loop[3,3:9]=True;loop[8,3:9]=True;loop[3:9,3]=True;loop[3:9,8]=True
    cycles,meta=skeleton_chains(loop)
    assert len(cycles)==1 and np.array_equal(cycles[0][0],cycles[0][-1])
    assert meta["endpoints"]==0


def test_centerline_candidate_has_real_width_color_move_and_export(tmp_path):
    path=source(tmp_path,3)
    evidence=analyze(path,colors=2,background="#ffffff")
    scene=build_candidate(bind_analysis(path,evidence),evidence,"asset","stroke_layers")
    strokes=[o for o in scene["objects"] if o.get("semantic",{}).get("role")=="observed_centerline"]
    assert strokes and any("C" in o["geometry"]["d"] for o in strokes)
    target=max(strokes,key=lambda o:len(o["geometry"]["d"]))
    original=render_pixels(scene)
    for properties in ({"style":{**target["style"],"stroke":"#2244ee"}},
                       {"style":{**target["style"],"stroke_width":7}},
                       {"transform":[1,0,0,1,5,0]}):
        edited=apply_patch(scene,{"base_revision":0,"operations":[{"id":target["id"],"set":properties}]})
        assert np.any(render_pixels(edited)!=original)
        assert [o for o in edited["objects"] if o["id"]!=target["id"]]==[o for o in scene["objects"] if o["id"]!=target["id"]]
    exported=export_scene(scene,[target["id"]])
    svg=to_svg(exported)
    assert "<image" not in svg and "data:" not in svg and "stroke-width" in svg
    assert np.count_nonzero(render_pixels(exported)[:,:,3])>0
    audit=evaluate(path,evidence,scene)
    assert audit["F"]["internal_boundary"]["coverage"]>0.85


def test_auto_repair_requires_boundary_gain_and_unchanged_non_target(tmp_path):
    path=source(tmp_path)
    before=reconstruct(path,plan(path,["flat_contour"]))
    before["objects"].append({"id":"unrelated","kind":"rect","geometry":{"x":0,"y":0,"width":2,"height":2},"style":{"fill":"#ffffff"}})
    frozen=copy.deepcopy(before)
    fixed,report=repair_scene(before,path,"asset",{"action":"auto"})
    assert before==frozen
    assert report["status"]=="repaired"
    assert report["history"][0]["action"]=="restore_internal_boundaries"
    assert report["after"]["F"]["internal_boundary"]["coverage"]>report["before"]["F"]["internal_boundary"]["coverage"]
    assert fixed["objects"][-1]==before["objects"][-1]
    again,rejected=repair_scene(fixed,path,"asset",{"action":"restore_internal_boundaries"})
    assert rejected["status"]=="needs_review" and again==fixed

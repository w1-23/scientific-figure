import copy
import json
import socket
import xml.etree.ElementTree as ET
from io import BytesIO

import pytest
from PIL import Image

from scientific_figure.cli import main
from scientific_figure.editing import apply_patch, export_scene
from scientific_figure.geometry import Geometry
from scientific_figure.project import load_project, write_project
from scientific_figure.render import render_svg
from scientific_figure.scene import validate
from scientific_figure.svg import to_svg


def scene():
    return {"schema_version":"0.1","revision":0,"canvas":{"width":140,"height":90,"background":"#ffffff"},
            "resources":[{"id":"shade","kind":"linear_gradient","start":[0,0],"end":[1,1],"stops":[[0,"#ff0000"],[1,"#0000ff"]]}],
            "objects":[
                {"id":"cell","kind":"group","transform":[1,0,0,1,10,10],"style":{"fill":"url(#shade)"}},
                {"id":"membrane","kind":"path","parent":"cell","geometry":{"d":"M0 0H50V50H0Z M15 15H35V35H15Z"},"style":{"fill_rule":"evenodd"}},
                {"id":"label","kind":"text","geometry":{"x":75,"y":30,"text":"Cell & nucleus"},"style":{"font_size":10}},
                {"id":"target","kind":"rect","geometry":{"x":95,"y":50,"width":20,"height":20}},
                {"id":"link","kind":"connector","geometry":{"from":"cell","to":"target"},"style":{"stroke":"#006644","stroke_width":2}}
            ]}


def test_compound_hole_and_live_text_render():
    svg=to_svg(scene())
    xml=ET.fromstring(svg)
    assert len(xml.findall('.//{*}text'))==1
    assert "Cell &amp; nucleus" in svg
    image=Image.open(BytesIO(render_svg(svg,1))).convert("RGB")
    assert image.getpixel((35,35))==(255,255,255)  # genuine hole, not an extra filled polygon
    assert image.getpixel((15,15))!=(255,255,255)
    assert not xml.findall('.//{*}image')


def test_edit_stable_identity_and_connector_dependency():
    original=scene()
    changed=apply_patch(original,{"base_revision":0,"operations":[{"id":"cell","set":{"transform":[1,0,0,1,20,15]}}]})
    assert changed["revision"]==1
    assert changed["objects"][1:]==original["objects"][1:]
    assert Geometry(changed).endpoints("link")!=Geometry(original).endpoints("link")
    assert [o["id"] for o in changed["objects"]]==[o["id"] for o in original["objects"]]
    with pytest.raises(ValueError,match="revision"):
        apply_patch(changed,{"base_revision":0,"operations":[]})


def test_export_preserves_ancestor_transform_gradient_and_hole():
    selected=export_scene(scene(),["membrane"])
    assert {o["id"] for o in selected["objects"]}=={"cell","membrane"}
    assert selected["resources"][0]["id"]=="shade"
    assert selected["objects"][0]["transform"]==[1,0,0,1,10,10]
    image=Image.open(BytesIO(render_svg(to_svg(selected),1))).convert("RGBA")
    assert image.getpixel((29,29))[3]==0
    assert image.getpixel((8,8))[3]>0


def test_connector_export_without_endpoint_objects():
    exported=export_scene(scene(),["link"])
    assert [o["id"] for o in exported["objects"]]==["link"]
    assert exported["objects"][0]["kind"]=="path"
    assert "marker-end" in to_svg(exported)
    render_svg(to_svg(exported),1)


def test_group_opacity_composites_once():
    s={"schema_version":"0.1","revision":0,"canvas":{"width":50,"height":40,"background":"#ffffff"},"objects":[
        {"id":"group","kind":"group","style":{"opacity":0.5,"fill":"#ff0000"}},
        {"id":"a","parent":"group","kind":"rect","geometry":{"x":0,"y":0,"width":30,"height":30}},
        {"id":"b","parent":"group","kind":"rect","geometry":{"x":20,"y":0,"width":30,"height":30}}]}
    image=Image.open(BytesIO(render_svg(to_svg(s),1))).convert("RGB")
    assert image.getpixel((10,10))==image.getpixel((25,10))


@pytest.mark.parametrize("bad",["duplicate","cycle","reference","nan","hidden_endpoint"])
def test_invalid_scenes_rejected(bad):
    s=scene()
    if bad=="duplicate":s["objects"].append(copy.deepcopy(s["objects"][0]))
    elif bad=="cycle":s["objects"][0]["parent"]="cell"
    elif bad=="reference":s["objects"][-1]["geometry"]["to"]="missing"
    elif bad=="nan":s["objects"][0]["transform"][0]=float("nan")
    else:s["objects"][0]["visible"]=False
    with pytest.raises(ValueError):validate(s)


def test_failed_write_preserves_project_and_receipts_detect_tampering(tmp_path):
    output=tmp_path/"project"
    write_project(scene(),output,scale=1)
    before=(output/"figure.svg").read_bytes()
    changed=apply_patch(scene(),{"base_revision":0,"operations":[{"id":"label","set":{"geometry":{"text":"Changed"}}}]})
    with pytest.raises(ValueError):write_project(changed,output,expected_revision=0,scale=100)
    assert (output/"figure.svg").read_bytes()==before
    assert load_project(output)["revision"]==0
    write_project(changed,output,expected_revision=0,scale=1)
    assert load_project(output)["revision"]==1
    (output/"figure.svg").write_text("changed externally")
    with pytest.raises(ValueError,match="changed outside"):load_project(output)


def test_bad_patch_does_not_mutate_source():
    s=scene();before=copy.deepcopy(s)
    with pytest.raises(ValueError):
        apply_patch(s,{"base_revision":0,"operations":[{"id":"label","set":{"geometry":{"text":"valid first"}}},{"id":"absent","set":{"visible":False}}]})
    assert s==before


def test_cpu_pipeline_with_python_network_blocked(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError("network access attempted")
    monkeypatch.setattr(socket,"socket",forbidden)
    project=tmp_path/"offline"
    write_project(scene(),project,scale=1)
    assert load_project(project)["revision"]==0


def test_cli_json_roundtrip_and_gpu_is_explicitly_unsupported(tmp_path,capsys):
    source=tmp_path/"scene.json";source.write_text(json.dumps(scene()))
    assert main(["draw",str(source),"--output",str(tmp_path/"p"),"--scale","1"])==0
    assert json.loads(capsys.readouterr().out)["status"]=="success"
    assert main(["validate",str(tmp_path/"p")])==0
    assert json.loads(capsys.readouterr().out)["object_count"]==5
    assert main(["draw",str(source),"--output",str(tmp_path/"gpu"),"--device","gpu"])==4
    assert json.loads(capsys.readouterr().out)["status"]=="unsupported"
    assert not (tmp_path/"gpu").exists()

import copy
import hashlib
import socket

import pytest
from PIL import Image, ImageDraw

from scientific_figure.reconstruct import reconstruct
from scientific_figure.svg import to_svg
from scientific_figure.render import render_svg


def fixture(tmp_path):
    path=tmp_path/"reference.png"
    image=Image.new("RGB",(100,80),"white");draw=ImageDraw.Draw(image)
    draw.rectangle((25,20,64,59),fill="#167c52");draw.rectangle((35,30,54,49),fill="white")
    image.save(path)
    return path,{"schema_version":"0.1","source":{"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"size":[100,80],"region":[20,15,50,50]},
                 "canvas":{"width":50,"height":50,"background":"#ffffff"},
                 "steps":[{"step_id":"outline","operation":"trace_region","object":{"id":"outline","kind":"group"},
                           "region":[0,0,50,50],"parameters":{"palette":["#ffffff","#167c52"],"min_area":1,"simplify":0.1}}]}


def test_pixel_reconstruction_is_bound_to_input_and_coordinate_map(tmp_path,monkeypatch):
    source,plan=fixture(tmp_path)
    def forbidden(*a,**k):raise AssertionError("network attempted")
    monkeypatch.setattr(socket,"socket",forbidden)
    scene=reconstruct(source,plan)
    assert scene["provenance"]["source_to_scene"]==[1,0,0,1,-20,-15]
    assert len(scene["objects"])==2
    assert scene["objects"][1]["geometry"]["d"].count("M")==2
    assert scene["objects"][1]["style"]["fill_rule"]=="evenodd"
    assert render_svg(to_svg(scene),1).startswith(b"\x89PNG")
    source.write_bytes(source.read_bytes()+b"different")
    with pytest.raises(ValueError,match="hash"):reconstruct(source,plan)


@pytest.mark.parametrize("defect",["size","region","step_region","dependency","id_collision","canvas","parameters"])
def test_incomplete_or_inconsistent_plan_rejected(tmp_path,defect):
    source,plan=fixture(tmp_path)
    if defect=="size":plan["source"]["size"]=[90,80]
    elif defect=="region":plan["source"]["region"]=[90,15,50,50]
    elif defect=="step_region":plan["steps"][0]["region"]=[20,0,50,50]
    elif defect=="dependency":plan["steps"][0]["depends_on"]=["future"]
    elif defect=="id_collision":
        another=copy.deepcopy(plan["steps"][0]);another["step_id"]="second";plan["steps"].append(another)
    elif defect=="canvas":plan["canvas"]["width"]=100
    else:plan["steps"][0]["parameters"]["unknown"]=1
    with pytest.raises(ValueError):reconstruct(source,plan)

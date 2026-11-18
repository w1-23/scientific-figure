from io import BytesIO
import math

import numpy as np
import pytest
from PIL import Image, ImageDraw

from scientific_figure.quality import reviewed_trace
from scientific_figure.strokes import fit_stroke


def image_file(tmp_path, shape):
    image=Image.new("RGB",(96,96),"white");draw=ImageDraw.Draw(image)
    if shape=="ellipse":draw.ellipse((12,17,84,78),fill="#228854")
    elif shape=="donut":
        draw.ellipse((10,10,86,86),fill="#228854");draw.ellipse((30,30,66,66),fill="white")
    elif shape=="concave":draw.polygon([(10,10),(85,10),(85,32),(40,32),(40,85),(10,85)],fill="#228854")
    elif shape=="stroke":draw.line((12,70,82,22),fill="#228854",width=5)
    elif shape=="curve":draw.arc((10,10,86,86),5,160,fill="#228854",width=4)
    path=tmp_path/(shape+".png");image.save(path);return path


@pytest.mark.parametrize("shape",["ellipse","donut","concave"])
def test_generic_curves_pass_rendered_topology_and_fidelity_checks(tmp_path,shape):
    nodes,summary=reviewed_trace(image_file(tmp_path,shape),[0,0,96,96],object_id="test",
                                parameters={"palette":["#ffffff","#228854"],"min_area":1})
    review=summary["self_review"]
    assert review["status"]=="passed"
    accepted=review["history"][-1]
    assert accepted["audit"]["topology_equal"]
    assert accepted["audit"]["worst_iou"]>=0.96
    if shape=="ellipse":assert any("C" in o["geometry"]["d"] for o in nodes)
    if shape=="donut":assert nodes[0]["geometry"]["d"].count("M")==2


def test_bad_candidate_triggers_bounded_repair_instead_of_false_pass(tmp_path):
    _,summary=reviewed_trace(image_file(tmp_path,"ellipse"),[0,0,96,96],object_id="test",
                             parameters={"palette":["#ffffff","#228854"],"simplify":8},
                             policy={"min_iou":1,"max_rgb_mae":0.00001,"max_boundary_error_px":0.01})
    review=summary["self_review"]
    assert len(review["history"])==3
    assert not review["history"][0]["accepted"]
    assert review["history"][-1]["strategy"]=="conservative_contours"
    assert review["status"]=="passed"
    assert review["history"][-1]["audit"]["worst_iou"]==1


def test_unmet_complexity_budget_is_reported_not_hidden(tmp_path):
    _,summary=reviewed_trace(image_file(tmp_path,"donut"),[0,0,96,96],object_id="test",
                             parameters={"palette":["#ffffff","#228854"]},policy={"max_segments":1})
    assert summary["self_review"]["status"]=="needs_review"
    assert "path_complexity_budget" in summary["self_review"]["history"][-1]["reasons"]


def test_color_budget_does_not_silently_recolor_scientific_regions(tmp_path):
    image=Image.new("RGB",(96,96),"white");draw=ImageDraw.Draw(image)
    draw.rectangle((5,5,40,80),fill="#ff0000");draw.rectangle((50,5,90,80),fill="#0000ff")
    path=tmp_path/"colors.png";image.save(path)
    nodes,summary=reviewed_trace(path,[0,0,96,96],object_id="test",
                                parameters={"palette":["#ffffff","#ff0000","#0000ff"]},policy={"color_budget":1})
    assert {n["style"]["fill"] for n in nodes}=={"#ff0000","#0000ff"}
    assert summary["self_review"]["status"]=="needs_review"


def test_thin_stroke_is_one_open_centerline_not_a_filled_polygon(tmp_path):
    node,summary=fit_stroke(image_file(tmp_path,"stroke"),[0,0,96,96],object_id="line",
                            parameters={"hsv":[[30,50,20],[100,255,255]],"stroke":"#228854"})
    assert node["kind"]=="path"
    assert "Z" not in node["geometry"]["d"]
    assert node["style"]["fill"]=="none"
    assert summary["centerline_deviation"]<1.2
    assert summary["status"]=="passed"


def test_curved_line_is_not_misrepresented_as_straight(tmp_path):
    with pytest.raises(ValueError,match="needs_input"):
        fit_stroke(image_file(tmp_path,"curve"),[0,0,96,96],object_id="line",
                   parameters={"hsv":[[30,50,20],[100,255,255]]})

from io import BytesIO
import hashlib
import math

import numpy as np
from PIL import Image,ImageDraw

from scientific_figure.curves import smooth_path
from scientific_figure.editing import export_scene
from scientific_figure.geometry import Geometry
from scientific_figure.reconstruct import reconstruct
from scientific_figure.render import render_svg
from scientific_figure.svg import to_svg


def test_irregular_small_rings_do_not_overrun_periodic_sampling():
    rng=np.random.default_rng(11)
    for count in range(3,45):
        angles=np.sort(rng.uniform(0,2*math.pi,count))
        points=[(30+(12+rng.random()*3)*math.cos(t),30+(12+rng.random()*3)*math.sin(t)) for t in angles]
        d="M"+" L".join(f"{x},{y}" for x,y in points)+" Z"
        path,report=smooth_path(d,tolerance=0.45)
        assert path.endswith("Z")
        assert report["segments_after"]>0
        assert "nan" not in path.lower()


def test_raster_coordinates_do_not_double_apply_parent_transform(tmp_path):
    image=Image.new("RGB",(50,50),"white");ImageDraw.Draw(image).ellipse((10,10,40,40),fill="#008844")
    source=tmp_path/"source.png";image.save(source)
    plan={"schema_version":"0.1","source":{"sha256":hashlib.sha256(source.read_bytes()).hexdigest(),"size":[50,50],"region":[0,0,50,50]},
          "canvas":{"width":50,"height":50},"steps":[
              {"step_id":"parent","operation":"construct","object":{"id":"parent","kind":"group","transform":[0,2,-2,0,30,20]}},
              {"step_id":"child","operation":"trace_region","object":{"id":"child","kind":"group","parent":"parent"},
               "region":[0,0,50,50],"parameters":{"palette":["#ffffff","#008844"]}}]}
    result=reconstruct(source,plan)
    assert np.allclose(Geometry(result).world("child"),[1,0,0,1,0,0])


def test_selected_open_path_keeps_arrowhead_inside_export_bounds():
    scene={"schema_version":"0.1","revision":0,"canvas":{"width":100,"height":40},"objects":[
        {"id":"arrow","kind":"path","geometry":{"d":"M10 20 L80 20"},"style":{"fill":"none","stroke":"#000000","stroke_width":3,"arrow_end":True}}]}
    exported=export_scene(scene,["arrow"],padding=0)
    image=Image.open(BytesIO(render_svg(to_svg(exported),1))).convert("RGBA")
    alpha=np.asarray(image)[:,:,3]
    assert not alpha[0].any() and not alpha[-1].any()
    assert not alpha[:,0].any() and not alpha[:,-1].any()

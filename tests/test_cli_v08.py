"""CLI exposure of existing validated 0.8 APIs, including negative-local fits."""
from scientific_figure.cli import execute, parser
from scientific_figure.project import load_project
from scientific_figure.geometry import Geometry


def test_recipe_build_fits_negative_local_geometry(tmp_path):
    out=tmp_path/'particle'
    result=execute(parser().parse_args(['recipe','build','particle','--output',str(out),'--scale','1']))
    scene=load_project(out)
    x,y,w,h=scene['canvas']['viewBox']
    a,b,c,d=Geometry(scene).bounds(result['recipe']['root_id'])
    assert x<a and y<b and x+w>c and y+h>d
    assert a<0 and b<0


def test_new_cli_catalogs_do_not_load_models():
    result=execute(parser().parse_args(['doctor']))
    assert result['capabilities']['scene_0_3'] and not result['capabilities']['ocr']
    assert len(execute(parser().parse_args(['recipe','list']))['recipes'])==15
    assert len(execute(parser().parse_args(['registry','list']))['entries'])==18

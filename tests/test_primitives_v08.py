"""Experimental geometry reports its limits; real holes/pixels survive normalization."""
from io import BytesIO
import pytest
from PIL import Image
from scientific_figure.primitives import normalize_fill, experimental_closed_offset, experimental_variable_outline, affine, namespaced_id
from scientific_figure.svg import to_svg
from scientific_figure.render import render_svg


def image(d):
    scene={'schema_version':'0.1','revision':0,'canvas':{'width':100,'height':100},'objects':[{'id':'p','kind':'path','geometry':{'d':d},'style':{'fill':'#224466'}}]}
    return Image.open(BytesIO(render_svg(to_svg(scene),1))).convert('RGBA')


def test_normalization_preserves_true_evenodd_hole():
    d=normalize_fill('M10 10H90V90H10Z M30 30H70V70H30Z',fill_rule='evenodd')
    raster=image(d)
    assert raster.getpixel((20,50))[3]==255 and raster.getpixel((50,50))[3]==0


def test_offset_positive_negative_collapse_are_explicit():
    d='M20 20H80V80H20Z'
    larger=experimental_closed_offset(d,5);smaller=experimental_closed_offset(d,-5)
    assert larger['status']=='experimental' and not larger['collapsed']
    assert image(larger['d']).getpixel((17,50))[3]>200
    assert image(smaller['d']).getpixel((22,50))[3]==0
    collapsed=experimental_closed_offset(d,-40)
    assert collapsed['collapsed'] and collapsed['contour_count_changed']


def test_variable_width_explicit_samples_round_caps():
    result=experimental_variable_outline([[20,50],[80,50]],[8,30])
    raster=image(result['d'])
    assert result['status']=='experimental' and raster.getpixel((80,38))[3]>200
    assert raster.getpixel((20,38))[3]==0
    with pytest.raises(ValueError,match='duplicate'):experimental_variable_outline([[0,0],[0,0]],[2,4])
    with pytest.raises(ValueError):experimental_variable_outline([[0,0],[1,1]],[2,-1])


def test_namespace_affine_bounds_and_invalid_input():
    assert namespaced_id('one','body')==namespaced_id('one','body')
    assert namespaced_id('one','body')!=namespaced_id('two','body')
    assert affine(x=5,y=6,sx=2)==[2,0,0,2,5,6]
    with pytest.raises(ValueError):affine(sx=0)
    with pytest.raises(ValueError):normalize_fill('M0 0L5 0',fill_rule='nonzero')

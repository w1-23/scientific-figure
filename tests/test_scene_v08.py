from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image
from scientific_figure.builder import SceneBuilder
from scientific_figure.editing import apply_patch, export_scene
from scientific_figure.geometry import Geometry
from scientific_figure.project import load_project, write_project
from scientific_figure.render import render_svg
from scientific_figure.scene import validate
from scientific_figure.svg import to_svg


def example():
    b = SceneBuilder(200, 120, schema_version='0.3')
    b.add_many([
        {'id': 'group', 'kind': 'group', 'transform': [2, 0, 0, 1, 10, 20], 'anchors': {'out': [30, 15]}},
        {'id': 'body', 'kind': 'rect', 'parent': 'group', 'geometry': {'x': 0, 'y': 0, 'width': 30, 'height': 30}, 'style': {'fill': 'url(#shine)'}},
        {'id': 'target', 'kind': 'rect', 'geometry': {'x': 150, 'y': 20, 'width': 30, 'height': 30}, 'anchors': {'in': [150, 35]}},
        {'id': 'link', 'kind': 'connector', 'geometry': {'from': 'group', 'to': 'target', 'from_anchor': 'port:out', 'to_anchor': 'port:in'}, 'style': {'stroke': '#123456', 'stroke_dasharray': [5, 5], 'stroke_dashoffset': 1}},
    ], resources=[{'id': 'shine', 'kind': 'radial_gradient', 'center': [.5, .5], 'radius': .5, 'focus': [.4, .4], 'stops': [[0, '#ffffff'], [1, '#005588']]}])
    return b


def test_closed_batch_is_atomic_and_forward_refs_are_local():
    b = SceneBuilder(100, 100)
    before = b.build()
    with pytest.raises(ValueError):
        b.add_many([{'id': 'bad', 'kind': 'group', 'parent': 'missing'}])
    assert b.build() == before
    nodes = [{'id': 'child', 'kind': 'rect', 'parent': 'root', 'geometry': {'x': 0, 'y': 0, 'width': 5, 'height': 5}}, {'id': 'root', 'kind': 'group'}]
    b.add_many(nodes)
    nodes[0]['geometry']['width'] = 88
    assert b.build()['objects'][0]['geometry']['width'] == 5
    before = b.build()
    with pytest.raises(ValueError): b.add_many([{'id': 'root', 'kind': 'group'}])
    assert b.build() == before


def test_ports_follow_transform_edit_and_persist(tmp_path):
    scene = example().build()
    assert Geometry(scene).endpoints('link') == ((70, 35), (150, 35))
    changed = apply_patch(scene, {'base_revision': 0, 'operations': [{'id': 'group', 'set': {'transform': [0, 2, -1, 0, 90, 10]}}]})
    assert Geometry(changed).endpoints('link') == ((75, 70), (150, 35))
    write_project(changed, tmp_path/'project', scale=1)
    assert load_project(tmp_path/'project') == changed
    selected = export_scene(changed, ['link'])
    assert selected['objects'][0]['kind'] == 'path'
    assert 'port:' not in to_svg(selected)
    assert render_svg(to_svg(selected), 1)


def test_port_clone_is_independent():
    b = example()
    b.clone('group', {'group': 'copy', 'body': 'copy-body'}, resource_ids={'shine': 'copy-shine'}, transform=[1, 0, 0, 1, 5, 6])
    scene = b.build()
    assert Geometry(scene).anchor('copy', 'port:out') == (35, 21)
    assert Geometry(scene).anchor('group', 'port:out') == (70, 35)
    scene['objects'][-2]['anchors']['out'][0] = 1
    assert b.build()['objects'][0]['anchors']['out'] == [30, 15]


def test_radial_and_dash_are_real_svg_and_png():
    svg = to_svg(example().build())
    assert 'radialGradient' in svg and 'stroke-dasharray="5 5"' in svg
    image = Image.open(BytesIO(render_svg(svg, 1))).convert('RGBA')
    assert image.getpixel((30, 33)) != image.getpixel((12, 21))
    # Connector starts at 70,35 and has visible dash gaps before its arrow head.
    alphas = [image.getpixel((x, 35))[3] for x in range(75, 125)]
    assert min(alphas) == 0 and max(alphas) > 0


@pytest.mark.parametrize('version', ['0.1', '0.2'])
def test_new_features_do_not_relax_old_scenes(version):
    scene = example().build(); scene['schema_version'] = version
    with pytest.raises(ValueError): validate(scene)


def test_invalid_ports_and_dash_rejected():
    scene = example().build()
    for field, value in [('stroke_dasharray', [0, 0]), ('stroke_dasharray', [-1, 3]), ('stroke_dashoffset', True)]:
        bad = deepcopy(scene); bad['objects'][-1]['style'][field] = value
        with pytest.raises(ValueError): validate(bad)
    bad = deepcopy(scene); bad['objects'][-1]['geometry']['from_anchor'] = 'port:missing'
    with pytest.raises(ValueError): validate(bad)
    bad = deepcopy(scene); bad['resources'][0]['focus'] = [2, 2]
    with pytest.raises(ValueError): validate(bad)


def test_port_target_parent_can_contain_its_connector():
    b = SceneBuilder(100, 100, schema_version='0.3')
    b.add_many([
        {'id': 'container', 'kind': 'group', 'anchors': {'left': [1, 2], 'right': [20, 2]}},
        {'id': 'link', 'kind': 'connector', 'parent': 'container', 'geometry': {'from': 'container', 'to': 'container', 'from_anchor': 'port:left', 'to_anchor': 'port:right'}}
    ])
    assert Geometry(b.build()).endpoints('link') == ((1, 2), (20, 2))

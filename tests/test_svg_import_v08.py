"""Explicit Scene0.3 SVG subset: radial paint and numeric dashed strokes."""
from io import BytesIO

from PIL import Image
import pytest
import resvg_py

from scientific_figure.fonts import font_info
from scientific_figure.render import render_svg
from scientific_figure.scene import validate
from scientific_figure.svg import to_svg
from scientific_figure.svg_import import import_svg


def svg(body, attrs=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="128" height="96" '
            f'viewBox="0 0 128 96" {attrs}>{body}</svg>')


def load(tmp_path, source, **kwargs):
    path = tmp_path / "input.svg"
    path.write_text(source, encoding="utf-8")
    return import_svg(path, **kwargs)


def pixels(source):
    info = font_info()
    return Image.open(BytesIO(resvg_py.svg_to_bytes(
        svg_string=source, zoom=1, skip_system_fonts=True,
        font_files=[info["path"]], font_family=info["family"]))).convert("RGBA")


def equal_pixels(source, scene):
    native = pixels(source)
    imported = Image.open(BytesIO(render_svg(to_svg(scene), 1))).convert("RGBA")
    assert imported.size == native.size
    assert imported.tobytes() == native.tobytes()


def radial(attributes="", *, stops=None):
    stops = ('<stop offset="0" stop-color="#204080"/>'
             '<stop offset="1" stop-color="#f0a030"/>') if stops is None else stops
    return f'<radialGradient id="paint" {attributes}>{stops}</radialGradient>'


@pytest.mark.parametrize("attributes, expected", [
    ("", {"center": [0.5, 0.5], "radius": 0.5, "focus": [0.5, 0.5]}),
    ('cx="50%" cy="25%" r="75%" fx="25%" fy="25%"',
     {"center": [0.5, 0.25], "radius": 0.75, "focus": [0.25, 0.25]}),
    ('cx="0.5" cy="0.25" r="0.75" fx="0.25"',
     {"center": [0.5, 0.25], "radius": 0.75, "focus": [0.25, 0.25]}),
])
def test_bounding_box_radial_defaults_fractions_and_forward_paint_match_png(tmp_path, attributes, expected):
    source = svg('<rect id="body" x="10" y="8" width="100" height="72" fill="url(#paint)"/>'
                 '<defs>' + radial(attributes) + '</defs>')
    scene = load(tmp_path, source, schema_version="0.3")
    assert validate(scene) is scene and scene["schema_version"] == "0.3"
    paint = scene["resources"][0]
    assert paint == {"id": "paint", "kind": "radial_gradient", **expected,
                     "units": "objectBoundingBox", "stops": [[0, "#204080"], [1, "#f0a030"]]}
    equal_pixels(source, scene)
    again = load(tmp_path, to_svg(scene), schema_version="0.3")
    equal_pixels(source, again)


def test_user_space_radial_numeric_geometry_and_px_match_native_png(tmp_path):
    source = svg('<defs>' + radial('gradientUnits="userSpaceOnUse" cx="64px" cy="48" r="40px" '
                                  'fx="52" fy="44" spreadMethod="pad"') + '</defs>'
                 '<g transform="translate(3 2)"><ellipse cx="64" cy="48" rx="50" ry="35" '
                 'fill="url(#paint)"/></g>')
    scene = load(tmp_path, source, schema_version="0.3")
    paint = scene["resources"][0]
    assert paint["units"] == "userSpaceOnUse"
    assert paint["center"] == [64, 48] and paint["focus"] == [52, 44]
    assert paint["radius"] == 40
    equal_pixels(source, scene)


def test_radial_and_linear_paint_references_coexist_with_rgba_stops(tmp_path):
    source = svg('<defs>' + radial(stops='<stop offset="0" stop-color="#20408080" stop-opacity="0.5"/>'
                                  '<stop offset="1" stop-color="red" stop-opacity="0.2"/>') +
                 '<linearGradient id="line"><stop offset="0" stop-color="blue"/>'
                 '<stop offset="1" stop-color="red"/></linearGradient></defs>'
                 '<rect x="10" y="10" width="90" height="60" fill="url(#paint)" stroke="url(#line)" '
                 'stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>')
    scene = load(tmp_path, source, schema_version="0.3")
    assert {r["kind"] for r in scene["resources"]} == {"radial_gradient", "linear_gradient"}
    assert scene["resources"][0]["stops"] == [[0, "#20408040"], [1, "#ff000033"]]
    equal_pixels(source, scene)


@pytest.mark.parametrize("dash, offset, expected", [
    ("8 4", "-3", [8, 4]),
    ("3, 1, 2", "2px", [3, 1, 2]),
    ("0 6", "0", [0, 6]),
    ("1e1 .5", "+1.25", [10, 0.5]),
])
def test_numeric_dash_array_and_offset_match_native_png(tmp_path, dash, offset, expected):
    source = svg(f'<path id="line" d="M10 20H110V70H10Z" fill="none" stroke="#204080" '
                 f'stroke-width="3" stroke-linecap="round" stroke-linejoin="round" '
                 f'stroke-dasharray="{dash}" stroke-dashoffset="{offset}"/>')
    scene = load(tmp_path, source, schema_version="0.3")
    style = next(o["style"] for o in scene["objects"] if o["id"] == "line")
    assert style["stroke_dasharray"] == expected
    assert isinstance(style["stroke_dashoffset"], float)
    equal_pixels(source, scene)
    equal_pixels(source, load(tmp_path, to_svg(scene), schema_version="0.3"))


def test_dash_inheritance_explicit_child_override_and_radial_stroke(tmp_path):
    source = svg('<defs>' + radial() + '</defs>'
                 '<g id="dashed" stroke="url(#paint)" fill="none" stroke-width="3" '
                 'stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="8 4" stroke-dashoffset="2">'
                 '<path id="inherited" d="M10 20H110"/>'
                 '<path id="override" d="M10 60H110" stroke-dasharray="2 3" stroke-dashoffset="-2"/>'
                 '</g>')
    # Give the painted paths a nondegenerate object bbox for radial paint.
    source = source.replace('M10 20H110', 'M10 20L110 30').replace('M10 60H110', 'M10 60L110 70')
    scene = load(tmp_path, source, schema_version="0.3")
    objects = {o["id"]: o for o in scene["objects"]}
    assert objects["dashed"]["style"]["stroke_dasharray"] == [8, 4]
    assert "stroke_dasharray" not in objects["inherited"]["style"]
    assert objects["override"]["style"]["stroke_dasharray"] == [2, 3]
    equal_pixels(source, scene)


def test_default_and_explicit_02_remain_strict_after_03_import(tmp_path):
    plain = svg('<rect width="10" height="10"/>')
    assert load(tmp_path, plain)["schema_version"] == "0.2"
    assert load(tmp_path, plain, schema_version="0.2")["schema_version"] == "0.2"
    assert load(tmp_path, plain, schema_version="0.3")["schema_version"] == "0.3"
    radial_source = svg('<defs>' + radial() + '</defs><rect width="10" height="10" fill="url(#paint)"/>')
    dashed = svg('<rect width="10" height="10" stroke-dasharray="3 2"/>')
    offset = svg('<rect width="10" height="10" stroke-dashoffset="2"/>')
    for source in (radial_source, dashed, offset):
        load(tmp_path, source, schema_version="0.3")
        for kwargs in ({}, {"schema_version": "0.2"}):
            with pytest.raises(ValueError, match="radialGradient|stroke-dash"):
                load(tmp_path, source, **kwargs)


@pytest.mark.parametrize("version", ["0.1", "0.4", "latest", "", None, True, 0.3, [], {}])
def test_wrong_schema_versions_and_types_fail_before_read(tmp_path, version):
    with pytest.raises(ValueError, match="schema_version"):
        import_svg(tmp_path / "nonexistent.svg", schema_version=version)


@pytest.mark.parametrize("attribute, value", [
    ("stroke-dasharray", "none"), ("stroke-dasharray", "inherit"),
    ("stroke-dasharray", "initial"), ("stroke-dasharray", ""),
    ("stroke-dasharray", "1 -2"), ("stroke-dasharray", "0 0"),
    ("stroke-dasharray", "1px 2px"), ("stroke-dasharray", "5% 2%"),
    ("stroke-dasharray", "1,,2"), ("stroke-dasharray", "[1,2]"),
    ("stroke-dasharray", "true"), ("stroke-dasharray", "NaN 2"),
    ("stroke-dasharray", "1e99 2"), ("stroke-dasharray", "1 " * 33),
    ("stroke-dashoffset", "none"), ("stroke-dashoffset", "5%"),
    ("stroke-dashoffset", "1 2"), ("stroke-dashoffset", "Infinity"),
])
def test_dash_values_outside_numeric_scene_contract_are_rejected(tmp_path, attribute, value):
    source = svg(f'<rect width="10" height="10" {attribute}="{value}"/>')
    with pytest.raises(ValueError, match=attribute) as error:
        load(tmp_path, source, schema_version="0.3")
    if value == "none" and attribute == "stroke-dasharray":
        assert "none is not supported" in str(error.value)


@pytest.mark.parametrize("attributes, attribute", [
    ('r="0"', 'r'), ('r="-1"', 'r'), ('r="NaN"', 'r'),
    ('cx="true"', 'cx'), ('r="0.5px"', 'r'),
    ('fx="100%"', 'fx/fy'),
    ('gradientUnits="unknown"', 'gradientUnits'),
    ('gradientUnits="userSpaceOnUse"', 'cx'),
    ('gradientUnits="userSpaceOnUse" cx="50%" cy="40" r="30"', 'cx'),
    ('gradientUnits="userSpaceOnUse" cx="50" cy="40"', 'r'),
    ('spreadMethod="reflect"', 'spreadMethod'),
    ('gradientTransform="scale(2)"', 'gradientTransform'),
    ('href="#another"', 'href'), ('fr="0"', 'fr'),
    ('stroke-dasharray="2 1"', 'stroke-dasharray'),
])
def test_radial_exact_whitelist_and_geometry_fail_explicitly(tmp_path, attributes, attribute):
    with pytest.raises(ValueError, match=attribute):
        load(tmp_path, svg('<defs>' + radial(attributes) + '</defs>'), schema_version="0.3")


@pytest.mark.parametrize("body, attribute", [
    ('<defs>' + radial() + '</defs><rect width="10" height="10" clip-path="url(#paint)"/>', 'clip-path'),
    ('<defs>' + radial() + '</defs><rect width="10" height="10" mask="url(#paint)"/>', 'mask'),
    ('<defs><clipPath id="clip"><rect width="5" height="5"/></clipPath></defs>'
     '<rect width="10" height="10" fill="url(#clip)"/>', 'fill'),
    ('<defs><mask id="mask" maskUnits="userSpaceOnUse" maskContentUnits="userSpaceOnUse" '
     'mask-type="alpha" x="0" y="0" width="20" height="20"><rect width="5" height="5"/></mask></defs>'
     '<rect width="10" height="10" stroke="url(#mask)" stroke-linecap="round" stroke-linejoin="round"/>', 'stroke'),
    ('<rect width="10" height="10" fill="url(#missing)"/>', 'fill'),
    ('<rect width="10" height="10" fill="url(https://example.invalid/paint)"/>', 'fill'),
    ('<defs>' + radial() + '</defs><rect width="10" height="10" fill="url(#paint) red"/>', 'fill'),
    ('<defs>' + radial() + '</defs><rect width="10" height="10" fill="url(#paint)" onclick="run()"/>', 'onclick'),
    (radial(), 'placement'),
])
def test_paint_and_effect_references_keep_exact_resource_types(tmp_path, body, attribute):
    with pytest.raises(ValueError, match=attribute):
        load(tmp_path, svg(body), schema_version="0.3")


def test_radial_stop_contract_and_budget(tmp_path):
    for stops in ('<stop offset="0"/>',
                  '<stop offset="1"/><stop offset="0"/>',
                  '<stop offset="0" stop-color="url(#paint)"/><stop offset="1"/>',
                  '<stop offset="0" stop-opacity="0.5"/><stop offset="1"/>',
                  ''.join('<stop offset="0"/>' for _ in range(257))):
        with pytest.raises(ValueError, match="stop"):
            load(tmp_path, svg('<defs>' + radial(stops=stops) + '</defs>'), schema_version="0.3")

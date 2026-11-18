from copy import deepcopy
from io import BytesIO

import pytest
from PIL import Image
import resvg_py

from scientific_figure.editing import apply_patch
from scientific_figure.fonts import font_info
from scientific_figure.geometry import Geometry, multiply
from scientific_figure.render import render_svg
from scientific_figure.scene import validate
from scientific_figure.svg import to_svg
from scientific_figure.svg_import import import_svg


def source(body, attributes="", *, width="100", height="100", viewbox="0 0 100 100"):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="{viewbox}" {attributes}>{body}</svg>')


def load(tmp_path, svg):
    path = tmp_path / "input.svg"
    path.write_text(svg, encoding="utf-8")
    return import_svg(path)


def native(svg):
    info = font_info()
    return Image.open(BytesIO(resvg_py.svg_to_bytes(
        svg_string=svg, zoom=1, skip_system_fonts=True,
        font_files=[info["path"]], font_family=info["family"]))).convert("RGBA")


def rendered(scene):
    return Image.open(BytesIO(render_svg(to_svg(scene), 1))).convert("RGBA")


def equal_pixels(svg, scene):
    a, b = native(svg), rendered(scene)
    assert a.size == b.size
    assert a.tobytes() == b.tobytes()
    return b


def test_compound_hole_group_compositing_forward_gradient_and_exact_pixels(tmp_path):
    svg = source('''<title>Editable source</title><desc>two independent paths</desc>
      <g id="unit" opacity="0.5" fill="url(#paint)" transform="translate(5 7)">
        <path id="hole" fill-rule="evenodd" d="M0 0H70V70H0Z M20 20H50V50H20Z"/>
        <path id="overlap" d="M5 5H15V15H5Z"/>
      </g>
      <defs><linearGradient id="paint" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0" stop-color="#286e84"/>
        <stop offset="1" stop-color="#d27c3e"/>
      </linearGradient></defs>''')
    s = load(tmp_path, svg)
    assert validate(s) is s and s["schema_version"] == "0.2"
    im = equal_pixels(svg, s)
    assert im.getpixel((40, 40))[3] == 0  # a real transparent hole
    assert im.getpixel((10, 10))[3] in {127, 128}
    assert im.getpixel((15, 15))[3] in {127, 128}  # overlap composites once
    assert s["provenance"]["svg_import"]["ignored_metadata"] == [
        {"tag": "title", "text": "Editable source"}, {"tag": "desc", "text": "two independent paths"}]
    assert "<svg" not in repr(s)
    before = deepcopy(s)
    changed = apply_patch(s, {"base_revision": 0, "operations": [
        {"id": "overlap", "set": {"transform": [1, 0, 0, 1, 75, 0]}}]})
    assert s == before
    assert next(o for o in changed["objects"] if o["id"] == "hole") == next(o for o in s["objects"] if o["id"] == "hole")
    assert rendered(changed).tobytes() != im.tobytes()


def test_clip_with_hole_alpha_mask_local_transform_and_pixels(tmp_path):
    svg = source('''<defs>
      <clipPath id="cut" clipPathUnits="userSpaceOnUse" transform="translate(2 0)">
        <path clip-rule="evenodd" d="M0 0H80V80H0Z M25 25H50V50H25Z"/>
      </clipPath>
      <mask id="fade" maskUnits="userSpaceOnUse" maskContentUnits="userSpaceOnUse"
            mask-type="alpha" x="0" y="0" width="100" height="100">
        <rect x="0" y="0" width="100" height="60" fill="#ff0000" fill-opacity="0.5"/>
        <path d="M0 10H20V30H0Z" fill="#ffffff80" opacity="0.5"/>
      </mask>
      </defs>
      <g id="unit" transform="matrix(1 0 0 1 5 7)" opacity="0.5">
        <rect id="body" x="0" y="0" width="90" height="90" fill="#286e84" clip-path="url(#cut)" mask="url(#fade)"/>
      </g>''')
    s = load(tmp_path, svg)
    im = equal_pixels(svg, s)
    assert im.getpixel((40, 40))[3] == 0
    assert im.getpixel((70, 75))[3] == 0
    assert im.getpixel((65, 20))[3] in {63, 64}
    assert {r["kind"] for r in s["resources"]} == {"clip_path", "alpha_mask"}
    # A self-contained Scene still renders after the input SVG has been removed.
    (tmp_path / "input.svg").unlink()
    assert rendered(s).tobytes() == im.tobytes()
    equal_pixels(to_svg(s), load(tmp_path, to_svg(s)))
    changed = deepcopy(s)
    changed["resources"][1]["shapes"][0]["alpha"] = .25
    assert changed["resources"][0] == s["resources"][0]
    assert rendered(changed).getpixel((65, 20))[3] < im.getpixel((65, 20))[3]
    moved = deepcopy(s)
    next(o for o in moved["objects"] if o["id"] == "unit")["transform"][4] += 4
    assert rendered(moved).getpixel((69, 20)) == im.getpixel((65, 20))


def test_named_colors_remain_deterministic_after_pillow_cache_mutation(tmp_path, monkeypatch):
    from PIL.ImageColor import colormap
    svg = source('<rect width="5" height="5" fill="red"/>')
    monkeypatch.setitem(colormap, "red", "#ff0000")
    before = load(tmp_path, svg)
    monkeypatch.setitem(colormap, "red", (255, 0, 0))
    assert load(tmp_path, svg) == before
    equal_pixels(svg, before)


def test_gradient_stop_alpha_normalization_exact_only(tmp_path):
    svg = source('''<defs><linearGradient id="p">
      <stop offset="0" stop-color="#ff000080" stop-opacity="0.5"/>
      <stop offset="100%" stop-color="blue" stop-opacity="0.2"/>
      </linearGradient></defs><rect width="100" height="100" fill="url(#p)"/>''')
    s = load(tmp_path, svg)
    assert s["resources"][0]["stops"] == [[0, "#ff000040"], [1, "#0000ff33"]]
    equal_pixels(svg, s)
    with pytest.raises(ValueError, match="stop.*stop-opacity.*RGBA8"):
        load(tmp_path, svg.replace('stop-opacity="0.2"', 'stop-opacity="0.5"'))


def test_shapes_transform_order_canvas_preservation_and_deterministic_ids(tmp_path):
    svg = source('''<g id="svg-import-1" transform="translate(10 12) scale(1.2 0.8) rotate(30 20 20)">
      <rect x="5" y="7" width="20" height="30" rx="3" ry="3" fill="red"/>
      <circle id="dot" cx="40" cy="30" r="5" fill="#286e84"/>
      <ellipse cx="55" cy="50" rx="9" ry="4" fill="green"/>
      <path d="M5 50Q20 30 30 50" fill="none" stroke="black" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round"/>
      </g>''', width="120px", height="90px", viewbox="-10 -5 130 100")
    a = load(tmp_path, svg)
    assert a == load(tmp_path, svg)
    assert a["canvas"] == {"width": 120, "height": 90, "viewBox": [-10, -5, 130, 100]}
    assert a["provenance"]["svg_import"]["source_canvas"]["width"] == "120px"
    assert len({o["id"] for o in a["objects"]}) == len(a["objects"])
    assert a["objects"][0]["id"] != "svg-import-1"
    equal_pixels(svg.replace('width="120px"', 'width="120"').replace('height="90px"', 'height="90"'), a)
    world = Geometry(a).world("dot")
    assert world == next(o for o in a["objects"] if o["id"] == "svg-import-1")["transform"]
    assert world != multiply([1.2, 0, 0, .8, 0, 0], [1, 0, 0, 1, 10, 12])


def test_selected_font_multiline_exact_positions_and_no_fallback(tmp_path):
    family = font_info()["family"]
    svg = source(f'''<g font-family="{family}" font-size="10" fill="#286e84">
      <text id="label" x="20" y="20" text-anchor="middle">First<tspan x="20" dy="12">Second</tspan></text>
      </g>''')
    s = load(tmp_path, svg)
    label = next(o for o in s["objects"] if o["id"] == "label")
    assert label["geometry"]["text"] == "First\nSecond"
    equal_pixels(svg, s)
    for bad in (svg.replace('dy="12"', 'dy="13"'), svg.replace('x="20" dy=', 'x="21" dy='),
                svg.replace(f'font-family="{family}"', 'font-family="serif"'),
                svg.replace(f'font-family="{family}"', ''),
                svg.replace('font-size="10"', 'font-size="10" font-weight="bold"')):
        with pytest.raises(ValueError, match="font-family|font-weight|tspan"):
            load(tmp_path, bad)


@pytest.mark.parametrize("content", ["  A  B  ", "A\nB", "A\tB", "A&#13;B", "A&#10;B"])
def test_text_whitespace_is_normalized_without_changing_pixels(tmp_path, content):
    svg = source(f'<text x="5" y="20" font-family="{font_info()["family"]}">{content}</text>')
    s = load(tmp_path, svg)
    equal_pixels(svg, s)


@pytest.mark.parametrize("anchor", ["start", "middle", "end"])
@pytest.mark.parametrize("first,second", [(" A  B ", " C "), ("A\nB", "C\tD"), ("A", "B")])
def test_multiline_whitespace_and_anchors_preserve_pixels(tmp_path, anchor, first, second):
    svg = source(f'<text x="50" y="20" text-anchor="{anchor}" font-family="{font_info()["family"]}">'
                 f'{first}<tspan x="50" dy="19.2">{second}</tspan></text>')
    if first.strip() != first or second.strip() != second:
        with pytest.raises(ValueError, match="whitespace"):
            load(tmp_path, svg)
    else:
        equal_pixels(svg, load(tmp_path, svg))


def test_root_compositing_transform_and_inherited_resource_styles(tmp_path):
    svg = source('''<defs><mask id="m" maskUnits="userSpaceOnUse" maskContentUnits="userSpaceOnUse"
        mask-type="alpha" x="0" y="0" width="100" height="100"><circle r="40" cx="40" cy="40"/></mask>
        <clipPath id="c"><ellipse cx="40" cy="40" rx="30" ry="20"/></clipPath></defs>
        <g mask="url(#m)" clip-path="url(#c)"><rect width="80" height="80"/><circle r="10" cx="20" cy="20"/></g>''',
        'fill="#286e8480" opacity="0.5" transform="translate(3 4)"')
    equal_pixels(svg, load(tmp_path, svg))


@pytest.mark.parametrize("body,attribute", [
    ('<script>alert(1)</script>', 'script'),
    ('<rect width="5" height="5" onclick="bad"/>', 'onclick'),
    ('<rect width="5" height="5" style="fill:red"/>', 'style'),
    ('<style>rect {fill:red}</style>', 'style'),
    ('<image href="file:///secret"/>', 'image'),
    ('<use href="#x"/>', 'use'),
    ('<symbol id="x"/>', 'symbol'),
    ('<foreignObject/>', 'foreignObject'),
    ('<filter/>', 'filter'),
    ('<rect width="5" height="5" filter="url(#x)"/>', 'filter'),
    ('<svg width="5" height="5"/>', 'placement'),
    ('<path d="M0 0H5V5Z" unknown="1"/>', 'unknown'),
    ('<rect width="1cm" height="5"/>', 'width'),
    ('<rect width="5" height="5" rx="1" ry="2"/>', 'rx/ry'),
    ('<path d="M0 0H5" stroke="red"/>', 'stroke-linecap'),
    ('<g transform="skewX(20)"/>', 'transform'),
    ('<g transform="scale(0)"/>', 'transform'),
    ('<g transform="translate(1e99)"/>', 'transform'),
    ('<g transform="scale(10000000)"><g transform="scale(10000000)"/></g>', 'transform'),
    ('<g transform=",translate(1)"/>', 'transform'),
    ('<g transform="translate(1),,scale(2)"/>', 'transform'),
    ('<path d="M0 0L1e99 0Z"/>', 'd'),
    ('<path d="M0 0LNaN 0Z"/>', 'd'),
    ('<path d="M0 0R10 10"/>', 'd'),
    ('<rect width="5" height="5" fill="url(https://evil/x)"/>', 'fill'),
    ('<rect width="5" height="5" fill="url(#missing)"/>', 'fill'),
    ('<rect width="5" height="5" mask="none"/>', 'mask'),
    ('<g id="dup"/><g id="dup"/>', 'id'),
    ('<g id="sf-private"/>', 'id'),
    ('<g id="bad:id"/>', 'id'),
    ('<metadata>private</metadata>', 'metadata'),
    ('<g><title>nested</title></g>', 'metadata'),
    ('<path d="M0 0H5Z">lost content</path>', 'text'),
    ('<g/>lost tail', 'tail'),
])
def test_unsupported_or_adversarial_features_are_never_dropped(tmp_path, body, attribute):
    with pytest.raises(ValueError, match=attribute):
        load(tmp_path, source(body))


@pytest.mark.parametrize("body,attribute", [
    ('<defs><linearGradient id="p" gradientUnits="userSpaceOnUse"/></defs>', 'gradientUnits'),
    ('<defs><linearGradient id="p" gradientTransform="scale(2)"/></defs>', 'gradientTransform'),
    ('<defs><linearGradient id="p" href="#x"/></defs>', 'href'),
    ('<defs><clipPath id="p" clipPathUnits="objectBoundingBox"><rect width="1" height="1"/></clipPath></defs>', 'clipPathUnits'),
    ('<defs><clipPath id="p"><path d="M0 0H10V10"/></clipPath></defs>', 'd'),
    ('<defs><clipPath id="p"><rect width="5" height="5"/><rect width="6" height="6"/></clipPath></defs>', 'children'),
    ('<defs><clipPath id="p" transform="scale(2)"><circle r="5"/></clipPath></defs>', 'transform'),
    ('<defs><mask id="m"/></defs>', 'maskUnits'),
    ('<defs><mask id="m" maskUnits="userSpaceOnUse" maskContentUnits="userSpaceOnUse" mask-type="luminance"/></defs>', 'mask-type'),
    ('<defs><linearGradient id="p"><stop offset="1"/><stop offset="0"/></linearGradient></defs>', 'offset'),
    ('<defs><clipPath id="p"><rect width="5" height="5"/></clipPath></defs><rect width="5" height="5" fill="url(#p)"/>', 'fill'),
    ('<defs><linearGradient id="p"><stop offset="0"/><stop offset="1"/></linearGradient></defs><rect width="5" height="5" clip-path="url(#p)"/>', 'clip-path'),
])
def test_resource_semantics_fail_with_source_tag_attribute(tmp_path, body, attribute):
    with pytest.raises(ValueError, match=attribute) as exc:
        load(tmp_path, source(body))
    assert "<" in str(exc.value) and "@" in str(exc.value)


@pytest.mark.parametrize("svg", [
    '<!DOCTYPE svg [<!ENTITY x "test">]>' + source('<text>&x;</text>'),
    '<?xml-stylesheet href="file:///a"?>' + source(''),
    source('<g xmlns="https://foreign.example"/>'),
    source('<g xmlns=""/>'),
    source('').replace('xmlns="http://www.w3.org/2000/svg"', 'xmlns="https://foreign.example"'),
    source('<g x:href="a"/>').replace('<svg ', '<svg xmlns:x="http://www.w3.org/1999/xlink" '),
    source('<rect width="5" height="5"/>', 'preserveAspectRatio="none"'),
    source('<g>'),
    '<?xml version="1.0" encoding="iso-8859-1"?>' + source(''),
])
def test_xml_namespace_and_viewport_security(tmp_path, svg):
    with pytest.raises(ValueError):
        load(tmp_path, svg)


def test_workload_budgets_before_large_geometry_evaluation(tmp_path):
    with pytest.raises(ValueError, match="1 MiB"):
        load(tmp_path, source(" " * 1_048_576))
    with pytest.raises(ValueError, match="element/depth"):
        load(tmp_path, source('<g/>' * 3000))
    with pytest.raises(ValueError, match="element/depth"):
        load(tmp_path, source('<g>' * 81 + '</g>' * 81))
    gradients = ''.join(f'<linearGradient id="p{i}"><stop offset="0"/><stop offset="1"/></linearGradient>' for i in range(257))
    with pytest.raises(ValueError, match="256 resources"):
        load(tmp_path, source('<defs>' + gradients + '</defs>'))
    path = 'M0 0H10V10Z' + ' ' * 100000
    with pytest.raises(ValueError, match="total path characters"):
        load(tmp_path, source(''.join(f'<path d="{path}"/>' for _ in range(3))))
    with pytest.raises(ValueError, match="64 transforms"):
        load(tmp_path, source('<g transform="' + 'translate(0) ' * 65 + '"/>'))
    with pytest.raises(ValueError, match="width"):
        load(tmp_path, source('', width="8193"))

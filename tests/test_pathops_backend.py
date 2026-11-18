import importlib
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from scientific_figure import pathops_backend as backend
from scientific_figure.geometry import local_bounds
from scientific_figure.render import render_svg
from scientific_figure.scene import validate
from scientific_figure.svg import to_svg

OUTER = "M10 10H90V90H10Z"
INNER = "M30 30H70V70H30Z"


def alpha(d, *, fill_rule="nonzero"):
    scene = {"schema_version": "0.1", "revision": 0,
             "canvas": {"width": 120, "height": 110, "background": "none"},
             "objects": ([{"id": "result", "kind": "path", "geometry": {"d": d},
                          "style": {"fill": "#000000", "fill_rule": fill_rule}}] if d else [])}
    validate(scene)
    return Image.open(BytesIO(render_svg(to_svg(scene), 1))).convert("RGBA").getchannel("A")


def bounds(d):
    return local_bounds({"kind": "path", "geometry": {"d": d}}, {})


def test_difference_is_a_real_hole_and_mirror_preserves_winding():
    result = backend.boolean("difference", OUTER, INNER)
    image = alpha(result)
    assert image.getpixel((20, 20)) == 255
    assert image.getpixel((50, 50)) == 0
    assert image.getpixel((5, 5)) == 0
    mirrored = backend.transformed(result, [-1, 0, 0, 1, 100, 0])
    assert alpha(mirrored).tobytes() == image.tobytes()
    # Reflect operands before boolean as well, including negative winding.
    assert alpha(backend.boolean("difference", backend.transformed(OUTER, [-1, 0, 0, 1, 100, 0]),
                                 backend.transformed(INNER, [-1, 0, 0, 1, 100, 0]))).tobytes() == image.tobytes()
    asymmetric = backend.boolean("difference", OUTER, "M20 30H50V70H20Z")
    flipped = alpha(backend.transformed(asymmetric, [-1, 0, 0, 1, 100, 0]))
    assert flipped.getpixel((35, 50)) == 255
    assert flipped.getpixel((65, 50)) == 0


@pytest.mark.parametrize("operation, expected", [
    ("union", (255, 255, 255)), ("intersection", (0, 255, 0)),
    ("difference", (255, 0, 0)), ("xor", (255, 0, 255))])
def test_boolean_operations_render_expected_coverage(operation, expected):
    result = backend.boolean(operation, "M10 10H60V60H10Z", "M40 10H90V60H40Z")
    image = alpha(result)
    assert tuple(image.getpixel(p) for p in [(20, 30), (50, 30), (80, 30)]) == expected


def test_fill_rules_and_multiple_contours():
    compound = OUTER + " " + INNER  # same winding on both contours
    empty = ""
    evenodd = backend.boolean("union", compound, empty, fill_rule="evenodd")
    winding = backend.boolean("union", compound, empty, fill_rule="nonzero")
    assert alpha(evenodd).getpixel((50, 50)) == 0
    assert alpha(winding).getpixel((50, 50)) == 255
    separate = backend.boolean("union", "M10 10H30V30H10Z", "M70 70H90V90H70Z")
    image = alpha(separate)
    assert image.getpixel((20, 20)) == image.getpixel((80, 80)) == 255
    assert image.getpixel((50, 50)) == 0


@pytest.mark.parametrize("operation, left, right", [
    ("difference", OUTER, OUTER), ("xor", OUTER, OUTER),
    ("intersection", OUTER, "M95 95H100V100H95Z"),
    ("union", "", ""), ("intersection", "", OUTER)])
def test_empty_is_explicit_and_renders_transparent(operation, left, right):
    result = backend.boolean(operation, left, right)
    assert result == ""
    assert alpha(result).getbbox() is None
    assert backend.transformed(result, [1, 0, 0, 1, 5, 5]) == ""
    assert backend.stroke_outline(result, 4) == ""


@pytest.mark.parametrize("offset", [0, -0.00001, 0.00001])
def test_tangent_and_nearly_coincident_edges_are_deterministic(offset):
    left = "M10 10H50V70H10Z"
    right = f"M{50 + offset} 10H90V70H{50 + offset}Z"
    result = backend.boolean("union", left, right)
    assert {backend.boolean("union", left, right) for _ in range(5)} == {result}
    image = alpha(result)
    assert image.getpixel((49, 40)) >= 254
    assert image.getpixel((50, 40)) >= 254
    assert image.getpixel((89, 40)) == 255
    intersection = alpha(backend.boolean("intersection", left, right))
    assert sum(intersection.tobytes()) < 100  # no invented overlap/sliver of visible area


def test_tangent_curves_do_not_invent_an_intersection():
    left = "M50 40A20 20 0 1 0 10 40A20 20 0 1 0 50 40Z"
    right = "M90 40A20 20 0 1 0 50 40A20 20 0 1 0 90 40Z"
    assert alpha(backend.boolean("intersection", left, right)).getbbox() is None
    united = alpha(backend.boolean("union", left, right))
    assert united.getpixel((30, 40)) == united.getpixel((70, 40)) == 255


def test_round_open_stroke_and_nonuniform_transform_semantics():
    source = "M20 30H60"
    outline = backend.stroke_outline(source, 10)
    assert bounds(outline) == pytest.approx([15, 25, 65, 35], abs=.0001)
    image = alpha(outline)
    assert image.getpixel((16, 29)) == 255  # round cap extends beyond endpoint
    assert image.getpixel((15, 25)) == 0   # cap is not square
    assert image.getpixel((19, 29)) == image.getpixel((60, 29)) == 255
    scale = [2, 0, 0, 3, 0, 0]
    local_then_scale = backend.transformed(outline, scale)
    scale_then_world_stroke = backend.stroke_outline(backend.transformed(source, scale), 10)
    assert bounds(local_then_scale) == pytest.approx([30, 75, 130, 105], abs=.001)
    assert bounds(scale_then_world_stroke) == pytest.approx([35, 85, 125, 95], abs=.001)
    assert alpha(local_then_scale).getpixel((50, 78)) == 255
    assert alpha(scale_then_world_stroke).getpixel((50, 78)) == 0
    corner = backend.stroke_outline("M20 50H50V20", 10)
    assert bounds(corner) == pytest.approx([15, 15, 55, 55], abs=.0001)
    assert alpha(corner).getpixel((54, 54)) == 0  # round join, not miter


def test_sources_and_backend_working_paths_are_unchanged(monkeypatch):
    original = backend._path
    captured = []

    def capture(*args, **kwargs):
        path = original(*args, **kwargs)
        captured.append((path, tuple(path.segments), path.fillType))
        return path

    monkeypatch.setattr(backend, "_path", capture)
    source = "M20 30Q40 10 60 30"
    result = backend.stroke_outline(source, 10)
    assert backend.stroke_outline(source, 10) == result
    boolean_result = backend.boolean("difference", OUTER, INNER)
    assert backend.boolean("difference", OUTER, INNER) == boolean_result
    for path, segments, fill_type in captured:
        assert tuple(path.segments) == segments
        assert path.fillType == fill_type
    assert source == "M20 30Q40 10 60 30"
    assert backend.transformed(source, [1, 0, 0, 1, 0, 0]) == "M20.0 30.0Q40.0 10.0 60.0 30.0"


@pytest.mark.parametrize("bad", [None, " ", "M0 0L", "L0 0", "M0 0X10 10Z",
                                  "M0 0Lnan 1Z", "M0 0L1e309 0Z", "M0 0H10000001Z",
                                  "M0 0Q10000001 0 1 1Z", "M0 0A10000001 1 0 0 1 1 1Z",
                                  "M0 0" + " L1 1" * 40000])
def test_invalid_nonfinite_or_oversized_paths_are_rejected(bad):
    with pytest.raises(ValueError):
        backend.boolean("union", bad, OUTER)


@pytest.mark.parametrize("open_path", ["M10 10H40V40H10", "M0 0H1Z M10 10L20 20", "M0 0H5V5Z L9 9"])
def test_closed_booleans_reject_any_open_contour(open_path):
    with pytest.raises(ValueError, match="closed"):
        backend.boolean("union", open_path, OUTER)


@pytest.mark.parametrize("matrix", [[1, 0, 0, 1], [1, 0, 0, 0, 0, 0],
                                     [1, 0, 0, 1, float("inf"), 0],
                                     [True, 0, 0, 1, 0, 0], [1, 0, 0, 1, 10000001, 0], "matrix"])
def test_unsupported_matrices_are_rejected(matrix):
    with pytest.raises(ValueError):
        backend.transformed(OUTER, matrix)


@pytest.mark.parametrize("kwargs", [{"fill_rule": "inverse"}, {"fill_rule": []},
                                     {"tolerance": 0}, {"tolerance": float("nan")},
                                     {"tolerance": True}])
def test_invalid_boolean_options(kwargs):
    with pytest.raises(ValueError):
        backend.boolean("union", OUTER, INNER, **kwargs)


@pytest.mark.parametrize("width", [0, -1, True, float("inf"), 10000001])
def test_invalid_stroke_width(width):
    with pytest.raises(ValueError):
        backend.stroke_outline("M0 0H10", width)


def test_invalid_operation_and_transformed_output_range():
    with pytest.raises(ValueError, match="operation"):
        backend.boolean("smooth", OUTER, INNER)
    with pytest.raises(ValueError):
        backend.transformed(OUTER, [10000000, 0, 0, 1, 0, 0])


def test_backend_info_is_honest_about_versions_and_error_bound():
    info = backend.backend_info()
    assert info["backend"] == "skia-pathops"
    assert info["version"] == importlib.metadata.version("skia-pathops")
    assert info["parser_version"] == importlib.metadata.version("fonttools")
    assert "no global error guarantee" in info["approximation"]


def test_cli_builder_and_transform_work_without_optional_backend():
    src = str(Path(__file__).resolve().parents[1] / "src")
    program = f'''
import importlib.abc
import sys
sys.path.insert(0, {src!r})
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "pathops":
            raise ModuleNotFoundError("intentionally unavailable")
sys.meta_path.insert(0, Block())
from scientific_figure.cli import main
from scientific_figure.builder import SceneBuilder
from scientific_figure.pathops_backend import boolean, transformed
assert SceneBuilder(20, 20).build()["schema_version"] == "0.1"
assert transformed("M0 0H1Z", [1, 0, 0, 1, 0, 0])
assert "pathops" not in sys.modules
try:
    main(["--help"])
except SystemExit as exc:
    assert exc.code == 0
try:
    boolean("union", "M0 0H1Z", "")
except ImportError as exc:
    assert "optional skia-pathops" in str(exc)
else:
    raise AssertionError("expected optional backend error")
print("lazy import ok")
'''
    result = subprocess.run([sys.executable, "-B", "-c", program], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "lazy import ok" in result.stdout

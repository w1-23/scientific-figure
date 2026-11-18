from io import BytesIO

import numpy as np
import pytest
from fontTools.pens.recordingPen import RecordingPen
from fontTools.svgLib.path import parse_path
from PIL import Image

from scientific_figure import pixel_regions
from scientific_figure.pixel_regions import pixel_paths
from scientific_figure.render import render_svg


def _rings(paths):
    rings = []
    for path in paths:
        pen = RecordingPen()
        parse_path(path, pen)
        points = None
        for op, args in pen.value:
            if op == "moveTo":
                assert points is None
                points = [args[0]]
            elif op == "lineTo":
                assert points is not None
                points.append(args[0])
            elif op == "closePath":
                assert points is not None
                if points[-1] == points[0]:
                    points.pop()
                assert len(points) >= 4
                rings.append(np.asarray(points))
                points = None
            else:
                pytest.fail(f"unexpected path operation: {op}")
        assert points is None
    return rings


def _area(ring):
    following = np.roll(ring, -1, axis=0)
    return float(np.sum(ring[:, 0] * following[:, 1] - following[:, 0] * ring[:, 1]) / 2)


def _raster(paths, width, height, scale=1, fill_rule="nonzero"):
    body = "".join(f'<path d="{d}" fill="black" fill-rule="{fill_rule}"/>' for d in paths)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
           f'{body}</svg>')
    return np.asarray(Image.open(BytesIO(render_svg(svg, scale))).convert("RGBA"))[:, :, 3]


def _assert_exact_render(mask, *, scale=1, fill_rule="nonzero"):
    alpha = _raster(pixel_paths(mask), mask.shape[1], mask.shape[0], scale, fill_rule)
    expected = np.repeat(np.repeat(mask != 0, scale, axis=0), scale, axis=1).astype(np.uint8) * 255
    error = np.abs(alpha.astype(int) - expected.astype(int))
    assert error.max() == 0
    assert np.count_nonzero(error) == 0


def test_single_pixel_uses_cell_boundaries():
    mask = np.zeros((4, 5), dtype=np.uint8)
    mask[2, 3] = 255
    assert pixel_paths(mask) == ["M3,2L4,2L4,3L3,3Z"]
    assert _area(_rings(pixel_paths(mask))[0]) == 1
    _assert_exact_render(mask, scale=4)


def test_one_pixel_bent_band_keeps_width_and_area():
    mask = np.zeros((20, 25), dtype=bool)
    mask[2:17, 3] = True
    mask[16, 3:21] = True
    mask[5:17, 20] = True
    mask[5, 14:21] = True
    mask[5:12, 14] = True
    rings = _rings(pixel_paths(mask))
    assert len(rings) == 1
    assert _area(rings[0]) == mask.sum()
    assert len(rings[0]) < mask.sum()
    for tolerance in (0, 0.35, 10):
        assert pixel_paths(mask, tolerance=tolerance) == pixel_paths(mask)
    _assert_exact_render(mask)
    _assert_exact_render(mask, scale=4)


def test_holes_and_nested_island_have_opposite_winding():
    mask = np.zeros((24, 30), dtype=bool)
    mask[1:23, 1:29] = True
    mask[4:20, 5:25] = False
    mask[8:16, 10:20] = True
    mask[10:14, 13:17] = False
    paths = pixel_paths(mask)
    assert len(paths) == 1
    areas = [_area(ring) for ring in _rings(paths)]
    assert len(areas) == 4
    assert sum(area > 0 for area in areas) == sum(area < 0 for area in areas) == 2
    assert sum(areas) == mask.sum()
    _assert_exact_render(mask, scale=3)
    _assert_exact_render(mask, scale=3, fill_rule="evenodd")


def test_diagonal_contact_takes_right_turn_and_does_not_splice_loops():
    mask = np.eye(9, dtype=bool)
    paths = pixel_paths(mask)
    rings = _rings(paths)
    assert len(paths) == 1 and len(rings) == 9
    assert all(len(ring) == 4 and _area(ring) == 1 for ring in rings)
    assert all(len(set(map(tuple, ring))) == len(ring) for ring in rings)
    _assert_exact_render(mask, scale=4)


def test_diagonal_holes_and_one_pixel_bridges_render_exactly():
    mask = np.ones((13, 15), dtype=bool)
    mask[2:5, 2:5] = False
    mask[5:8, 5:8] = False  # holes meet at a single vertex
    mask[8:11, 8:11] = False
    mask[0:7, 12] = False
    mask[8:13, 12] = False  # a one-pixel bridge across a slot
    assert sum(_area(ring) for ring in _rings(pixel_paths(mask))) == mask.sum()
    _assert_exact_render(mask, scale=4)
    _assert_exact_render(mask, scale=4, fill_rule="evenodd")


def test_all_three_by_three_masks_preserve_area_and_axis_aligned_boundaries():
    for bits in range(512):
        mask = ((bits >> np.arange(9)) & 1).reshape(3, 3)
        paths = pixel_paths(mask)
        assert len(paths) == bool(bits)
        rings = _rings(paths)
        assert sum(_area(ring) for ring in rings) == mask.sum()
        for ring in rings:
            assert np.array_equal(ring, ring.astype(int))
            edges = np.roll(ring, -1, axis=0) - ring
            assert np.all(np.count_nonzero(edges, axis=1) == 1)
            # Consecutive collinear edges have been merged, including the seam.
            assert np.all(edges[:, 0] * np.roll(edges[:, 1], -1)
                          - edges[:, 1] * np.roll(edges[:, 0], -1) != 0)


@pytest.mark.parametrize("fill_rule", ["nonzero", "evenodd"])
def test_all_local_topologies_have_zero_real_renderer_pixel_error(fill_rule):
    # Atlas covers every 3x3 mask and every possible diagonal multi-exit case.
    # Separate calls retain their own subpaths before a single real resvg render.
    paths = []
    expected = np.zeros((80, 160), dtype=bool)
    for bits in range(512):
        mask = np.zeros_like(expected)
        y, x = (bits // 32) * 5 + 1, (bits % 32) * 5 + 1
        mask[y:y+3, x:x+3] = ((bits >> np.arange(9)) & 1).reshape(3, 3)
        expected |= mask
        paths.extend(pixel_paths(mask))
    for scale in (1, 3):
        alpha = _raster(paths, 160, 80, scale, fill_rule)
        target = np.repeat(np.repeat(expected, scale, axis=0), scale, axis=1) * 255
        assert np.max(np.abs(alpha.astype(int) - target)) == 0


def test_complex_random_region_and_staircase_are_exact_and_deterministic():
    rng = np.random.default_rng(728)
    mask = rng.random((64, 96)) > 0.43
    mask[20:44, 15:80] = True
    mask[24:40, 20:75] = False
    for y in range(45, 62):
        mask[y, y-20:y+4] = True
    original = mask.copy()
    result = pixel_paths(mask)
    assert result == pixel_paths(np.asfortranarray(mask))
    assert result == pixel_paths(mask.astype(np.uint8) * 255)
    assert result == pixel_paths(mask.tolist())
    assert np.array_equal(mask, original)
    _assert_exact_render(mask)
    _assert_exact_render(mask, scale=3)


def test_full_size_rectangle_cancels_internal_edges_and_merges_collinear_edges():
    mask = np.ones((512, 512), dtype=bool)
    assert pixel_paths(mask) == ["M0,0L512,0L512,512L0,512Z"]


@pytest.mark.parametrize("mask", [np.zeros((0, 0)), np.zeros((0, 10)),
                                  np.zeros((10, 0)), np.zeros((8, 9)), []])
def test_empty_masks(mask):
    if isinstance(mask, list):
        with pytest.raises(ValueError, match="2-D"):
            pixel_paths(mask)
    else:
        assert pixel_paths(mask) == []


def test_finite_real_nonzero_values_are_occupied_and_input_is_read_only():
    mask = np.array([[0, -0.5], [2.5, 0]])
    mask.flags.writeable = False
    assert pixel_paths(mask) == pixel_paths(mask != 0)
    assert len(_rings(pixel_paths(mask))) == 2


@pytest.mark.parametrize("mask", [None, True, 1, [1, 0], np.zeros((2, 2, 1)),
    [[1], [1, 2]], [["yes"]], [[complex(1, 0)]], [[object()]],
    [[np.nan]], [[np.inf]], [[-np.inf]]])
def test_invalid_masks_fail_explicitly(mask):
    with pytest.raises(ValueError):
        pixel_paths(mask)


@pytest.mark.parametrize("tolerance", [-1, np.nan, np.inf, -np.inf, True, np.bool_(False),
                                        "0.35", None, 1 + 0j, 10**1000])
def test_invalid_tolerances_fail_even_for_empty_masks(tolerance):
    with pytest.raises(ValueError, match="tolerance"):
        pixel_paths(np.zeros((2, 2)), tolerance=tolerance)


@pytest.mark.parametrize("shape", [(513, 1), (1, 513), (0, 513)])
def test_dimension_limit(shape):
    with pytest.raises(ValueError, match="dimensions"):
        pixel_paths(np.zeros(shape, dtype=bool))


def test_actual_boundary_and_output_limits_fail_without_partial_result():
    y, x = np.indices((512, 512))
    with pytest.raises(ValueError, match="boundary edge budget"):
        pixel_paths((x + y) % 2 == 0)
    y, x = np.indices((192, 192))
    with pytest.raises(ValueError, match="output segment budget"):
        pixel_paths((x + y) % 2 == 0)


def test_budget_equality_and_collinear_compression(monkeypatch):
    monkeypatch.setattr(pixel_regions, "_MAX_BOUNDARY_EDGES", 8)
    monkeypatch.setattr(pixel_regions, "_MAX_OUTPUT_SEGMENTS", 4)
    assert pixel_paths(np.ones((1, 3))) == ["M0,0L3,0L3,1L0,1Z"]
    with pytest.raises(ValueError, match="boundary edge budget"):
        pixel_paths(np.ones((1, 4)))
    monkeypatch.setattr(pixel_regions, "_MAX_BOUNDARY_EDGES", 100)
    with pytest.raises(ValueError, match="output segment budget"):
        pixel_paths(np.eye(2))

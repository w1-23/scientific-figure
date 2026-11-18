from io import BytesIO

import cv2
import numpy as np
import pytest
from fontTools.pens.recordingPen import RecordingPen
from fontTools.svgLib.path import parse_path
from PIL import Image

from scientific_figure.curves import smooth_path
from scientific_figure.render import render_svg


def polygon(points):
    return "M" + "L".join(f"{x:.12g},{y:.12g}" for x, y in points) + "Z"


def ellipse(rx=45, ry=30, n=180):
    t = np.arange(n) * (2 * np.pi / n)
    return np.column_stack((64 + rx * np.cos(t), 48 + ry * np.sin(t)))


def test_ellipse_compression_determinism_and_seam():
    d = polygon(ellipse())
    result, summary = smooth_path(d)
    assert (result, summary) == smooth_path(d)
    assert summary["segments_before"] == 180
    assert 1 < summary["cubic_segments"] == summary["segments_after"] < 30
    assert summary["tolerance"] == 0.8 and summary["limitations"]
    pen = RecordingPen()
    parse_path(result, pen)
    start = np.array(pen.value[0][1][0])
    first = np.array(pen.value[1][1][0]) - start
    last = start - np.array(pen.value[-2][1][-2])
    assert np.dot(first, last) / np.linalg.norm(first) / np.linalg.norm(last) > 0.99999


@pytest.mark.parametrize("d", ["M10,10L110,10L110,80L10,80Z",
    "M10,10L50,10L110,10L110,40L110,80L50,80L10,80L10,40Z"])
def test_rectangle_keeps_corners_and_lines(d):
    result, summary = smooth_path(d)
    assert summary["cubic_segments"] == 0 and result.count("L") == 4
    assert all(point in result for point in ("10,10", "110,10", "110,80", "10,80"))


def test_pixel_staircase_is_not_all_corners():
    mask = np.zeros((96, 128), np.uint8)
    cv2.ellipse(mask, (64, 48), (45, 30), 0, 0, 360, 255, -1)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    _, summary = smooth_path(polygon(contours[0].reshape(-1, 2)))
    assert summary["cubic_segments"] > 0
    assert summary["segments_after"] < summary["segments_before"] / 4


def test_hole_and_resvg_comparison():
    d = polygon(ellipse()) + polygon(ellipse(18, 12)[::-1])
    result, _ = smooth_path(d)
    assert result.count("M") == result.count("Z") == 2
    def raster(path):
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="128" height="96">'
               f'<path d="{path}" fill="black" fill-rule="nonzero"/></svg>')
        return np.array(Image.open(BytesIO(render_svg(svg, 2))).convert("RGBA"))[:, :, 3] > 127
    before, after = raster(d), raster(result)
    assert (before & after).sum() / (before | after).sum() > 0.97
    assert not after[96, 128] and after[96, 200] and not after[0, 0]


def test_recursion_cap_retains_polygon(monkeypatch):
    from scientific_figure import curves
    monkeypatch.setattr(curves, "_MAX_DEPTH", 0)
    result, summary = smooth_path(polygon(ellipse(n=80)))
    assert result.count("L") == 80 and summary["cubic_segments"] == 0
    assert any("cap" in item.lower() for item in summary["limitations"])


@pytest.mark.parametrize("d,kwargs", [("", {}), (None, {}),
    ("M0,0L10,0L0,10", {}), ("M0,0L10,0Z", {}),
    ("M0,0L10,0L0,10ZM20,20L30,20L20,30", {}),
    ("M0,0C1,2 3,4 5,6Z", {}), ("M0,0L1e999,0L0,10Z", {}),
    ("M0,0Lnan,0L0,10Z", {}), ("M0,0L10,0L0,10Z?", {}),
    *[("M0,0L10,0L0,10Z", {key: value}) for key, value in
      [("tolerance", 0), ("tolerance", -1), ("tolerance", float("inf")),
       ("tolerance", float("nan")), ("corner_angle", 180), ("corner_angle", -1)]]])
def test_invalid_input(d, kwargs):
    with pytest.raises(ValueError):
        smooth_path(d, **kwargs)

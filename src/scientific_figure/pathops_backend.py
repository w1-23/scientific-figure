"""Optional SVG geometry helpers, independent of the Scene builder.

Skia uses float32 coordinates. fontTools converts SVG arcs to cubic Beziers
(up to 90 degrees per segment); tolerance only controls Skia conic-to-quadratic
conversion. It is NOT a guaranteed SVG-space error bound. No smoothing or
simplification pass is applied. Empty geometry is represented by ``""``.
"""
from __future__ import annotations

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version

from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.svgLib.path import parse_path
from fontTools.svgLib.path.parser import _tokenize_path

from .geometry import inverse
from .scene import number, validate


def backend_info():
    """Report installed distribution versions without loading optional pathops."""
    def installed(name):
        try:
            return version(name)
        except PackageNotFoundError:
            return None
    return {"backend": "skia-pathops", "version": installed("skia-pathops"),
            "svg_parser": "fontTools", "parser_version": installed("fonttools"),
            "coordinate_precision": "float32 (Skia)",
            "approximation": "SVG arcs to cubics; tolerance controls conics to quads only; no global error guarantee"}


def _backend():
    try:
        return import_module("pathops")
    except ImportError as exc:
        raise ImportError("geometry operations require the optional skia-pathops package") from exc


def _recording(d, *, closed=False):
    if not isinstance(d, str) or len(d) > 200_000:
        raise ValueError("path must be SVG d text of at most 200000 characters")
    pen = RecordingPen()
    if d == "":
        return pen
    try:
        # Reuse fontTools' tokenizer, including its compact SVG arc flags.
        # Check raw operands as well as resulting controls/bounds.
        for token in _tokenize_path(d):
            if len(token) != 1 or token not in "MmZzLlHhVvCcSsQqTtAa":
                number(float(token), "path operand")
        validate({"schema_version": "0.1", "revision": 0,
                  "canvas": {"width": 1, "height": 1},
                  "objects": [{"id": "path", "kind": "path", "geometry": {"d": d}}]})
        parse_path(d, pen)
        for command, points in pen.value:
            if closed and command == "endPath":
                raise ValueError("boolean operands require explicitly closed contours (Z)")
            for xy in points:
                for coordinate in xy:
                    number(coordinate, "path coordinate")
    except (ValueError, TypeError, IndexError, OverflowError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid SVG path: {exc}") from exc
    return pen


def _path(d, backend, *, closed=False, fill_rule="nonzero"):
    recording = _recording(d, closed=closed)
    result = backend.Path()
    recording.replay(result.getPen())
    result.fillType = {"nonzero": backend.FillType.WINDING,
                       "evenodd": backend.FillType.EVEN_ODD}[fill_rule]
    return result


def _tolerance(value):
    number(value, "tolerance", positive=True)


def _svg(path, tolerance):
    # Both stroke() and convertConicsToQuads() mutate; own every working path.
    result = _backend().Path(path)
    result.convertConicsToQuads(tolerance)
    pen = SVGPathPen(None)
    result.draw(pen)
    d = pen.getCommands()
    _recording(d)
    return d


def boolean(operation, left_d, right_d, *, fill_rule="nonzero", tolerance=.25):
    """Return union/intersection/difference/xor of explicitly closed SVG paths.

    Both operands use fill_rule. Output uses nonzero winding, including holes.
    Empty operands/results are allowed as ``""``; never add that as a Scene path.
    """
    names = {"union": "UNION", "intersection": "INTERSECTION",
             "difference": "DIFFERENCE", "xor": "XOR"}
    if not isinstance(operation, str) or operation not in names:
        raise ValueError("operation must be union, intersection, difference or xor")
    if not isinstance(fill_rule, str) or fill_rule not in {"nonzero", "evenodd"}:
        raise ValueError("fill_rule must be nonzero or evenodd")
    _tolerance(tolerance)
    backend = _backend()
    left = _path(left_d, backend, closed=True, fill_rule=fill_rule)
    right = _path(right_d, backend, closed=True, fill_rule=fill_rule)
    result = backend.op(left, right, getattr(backend.PathOp, names[operation]))
    return _svg(result, tolerance)


def stroke_outline(d, width, *, tolerance=.25):
    """Outline a local-space stroke using round caps/joins only.

    Width is measured before any transform. For a nonuniform transform, call
    transformed(stroke_outline(d, width), matrix) to scale the outline itself;
    stroking transformed(d, matrix) instead gives a constant world-space width.
    """
    number(width, "stroke width", positive=True)
    _tolerance(tolerance)
    backend = _backend()
    source = _path(d, backend)
    result = backend.Path(source)
    result.stroke(width, backend.LineCap.ROUND_CAP, backend.LineJoin.ROUND_JOIN, 4)
    return _svg(result, tolerance)


def transformed(d, matrix):
    """Apply a finite, nonsingular SVG [a,b,c,d,e,f] affine transform.

    Uses fontTools pens without loading Skia or reducing curves to polylines.
    Mirroring preserves contour relationships and therefore nonzero holes.
    """
    if not isinstance(matrix, (list, tuple)) or len(matrix) != 6:
        raise ValueError("matrix must contain six affine coefficients")
    for value in matrix:
        number(value, "matrix coefficient")
    inverse(matrix)  # same singularity threshold as the existing geometry engine
    pen = SVGPathPen(None)
    _recording(d).replay(TransformPen(pen, matrix))
    result = pen.getCommands()
    _recording(result)
    return result

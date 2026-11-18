"""Approximate closed polygon rings with corner-aware cubic Bezier paths."""
import math
import re

import numpy as np
from fontTools.pens.recordingPen import RecordingPen
from fontTools.svgLib.path import parse_path

_MAX_DEPTH = 20


def _rings(d):
    token = r"[MLZmlz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[\s,]"
    if not isinstance(d, str) or not d.strip() or re.sub(token, "", d):
        raise ValueError("expected a nonempty closed M L Z path")
    pen = RecordingPen()
    try:
        parse_path(d, pen)
    except (ValueError, IndexError, TypeError, AssertionError) as exc:
        raise ValueError("invalid polygon path") from exc
    rings, points = [], None
    for op, args in pen.value:
        if op == "moveTo" and points is None:
            points = [args[0]]
        elif op == "lineTo" and points is not None:
            if args[0] != points[-1]:
                points.append(args[0])
        elif op == "closePath" and points is not None:
            if points[-1] == points[0]:
                points.pop()
            p = np.asarray(points, dtype=float)
            if len(p) < 3 or not np.isfinite(p).all():
                raise ValueError("rings need at least three finite vertices")
            rings.append(p)
            points = None
        else:
            raise ValueError("only explicitly closed polygon subpaths are supported")
    if points is not None or not rings:
        raise ValueError("open or empty polygon path")
    return rings


def _unit(v):
    length = np.linalg.norm(v)
    return v / length if length > 0 else np.zeros(2)


def _features(p, tolerance, angle):
    """Estimate directions over arc-length neighborhoods, not pixel steps."""
    lengths = np.linalg.norm(np.roll(p, -1, axis=0) - p, axis=1)
    s = np.r_[0., np.cumsum(lengths)]
    perimeter = s[-1]
    if not math.isfinite(perimeter) or perimeter <= 0:
        raise ValueError("ring extent is degenerate or too large")
    span = min(perimeter / 12, max(4 * tolerance, 3 * np.median(lengths)))
    closed = np.vstack((p, p[0]))

    def sample(at):
        at = at % perimeter
        # Modulo can round a tiny negative offset to exactly perimeter.
        i = min(len(lengths) - 1, max(0, int(np.searchsorted(s, at, side="right") - 1)))
        return closed[i] + (closed[i + 1] - closed[i]) * ((at - s[i]) / lengths[i])

    incoming = np.array([_unit(v - sample(t - span)) for v, t in zip(p, s)])
    outgoing = np.array([_unit(sample(t + span) - v) for v, t in zip(p, s)])
    turns = np.degrees(np.arccos(np.clip((incoming * outgoing).sum(axis=1), -1, 1)))
    corners = []
    for i in sorted(np.flatnonzero(turns >= angle), key=lambda i: (-turns[i], i)):
        if all(min(abs(s[i] - s[j]), perimeter - abs(s[i] - s[j])) >= span
               for j in corners):
            corners.append(int(i))
    cuts = corners.copy() or [int(np.argmin(p[:, 0]))]
    if len(cuts) == 1:
        opposite = (s[cuts[0]] + perimeter / 2) % perimeter
        cuts.append(int(np.argmin(np.minimum(abs(s[:-1] - opposite),
                                              perimeter - abs(s[:-1] - opposite)))))
    tangents = np.array([_unit(a + b) for a, b in zip(incoming, outgoing)])
    return sorted(set(cuts)), set(corners), incoming, outgoing, tangents


def _fit(p, tangents, start, end, tolerance, depth=0):
    chord = p[-1] - p[0]
    square = float(chord @ chord)
    projection = np.clip((p - p[0]) @ chord / square, 0, 1) if square else np.zeros(len(p))
    if np.linalg.norm(p - (p[0] + projection[:, None] * chord), axis=1).max() <= tolerance:
        return [("L", p[-1:])], False
    if depth >= _MAX_DEPTH:
        return [("L", v[None, :]) for v in p[1:]], True
    lengths = np.linalg.norm(np.diff(p, axis=0), axis=1)
    u = np.r_[0., np.cumsum(lengths)] / lengths.sum()
    b = np.array([(1-u)**3, 3*(1-u)**2*u, 3*(1-u)*u*u, u**3]).T
    base = (b[:, 0] + b[:, 1])[:, None] * p[0] + (b[:, 2] + b[:, 3])[:, None] * p[-1]
    a = np.stack((b[:, 1, None] * start, -b[:, 2, None] * end), axis=-1)
    handles = np.linalg.lstsq(a.reshape(-1, 2), (p - base).ravel(), rcond=None)[0]
    if np.any(handles <= 1e-9) or np.any(handles > 3 * lengths.sum()):
        handles = np.full(2, math.sqrt(square) / 3)
    controls = np.array([p[0], p[0] + handles[0] * start,
                         p[-1] - handles[1] * end, p[-1]])
    error = np.linalg.norm(b @ controls - p, axis=1)
    split = int(np.argmax(error[1:-1])) + 1
    if error[split] <= tolerance:
        return [("C", controls[1:])], False
    left, cap1 = _fit(p[:split+1], tangents[:split+1], start,
                      tangents[split], tolerance, depth+1)
    right, cap2 = _fit(p[split:], tangents[split:], tangents[split],
                       end, tolerance, depth+1)
    return left + right, cap1 or cap2


def fit_open_path(points, *, tolerance=0.35):
    """Fit observed centerline samples; endpoint positions remain fixed.

    Residual is sampled, not a certified between-sample bound. The caller must
    compare rendered source evidence, especially at branches and tight bends.
    """
    p = np.asarray(points, dtype=float)
    if (p.ndim != 2 or p.shape[1] != 2 or len(p) < 2 or len(p) > 40000
            or not np.isfinite(p).all() or isinstance(tolerance, bool)
            or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance <= 0):
        raise ValueError("open curve needs finite 2D samples and positive tolerance")
    p = p[np.r_[True, np.any(np.diff(p, axis=0) != 0, axis=1)]]
    if len(p) < 2:
        raise ValueError("degenerate open curve")
    tangents = np.array([_unit(p[min(len(p)-1, i+2)]-p[max(0, i-2)]) for i in range(len(p))])
    commands, _ = _fit(p, tangents, tangents[0], tangents[-1], tolerance)
    def coords(values):
        return " ".join(f"{x:.9g},{y:.9g}" for x,y in values)
    return "M"+coords(p[:1])+" "+" ".join(command+coords(values) for command,values in commands)


def smooth_path(d: str, *, tolerance: float = 0.8,
                corner_angle: float = 55.0) -> tuple[str, dict]:
    """Fit each ring independently, retaining traversal order and explicit closure.

    Tolerance measures input-vertex residuals, not a certified Hausdorff bound.
    corner_angle is a minimum turning angle in degrees. Callers should render
    and compare topology/IoU before accepting the approximation.
    """
    for name, value in (("tolerance", tolerance), ("corner_angle", corner_angle)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            raise ValueError(f"{name} must be finite and positive")
    if corner_angle >= 180:
        raise ValueError("corner_angle must be less than 180 degrees")
    rings = _rings(d)
    parts, before, after, cubics, capped = [], 0, 0, 0, False

    def coords(points):
        if not np.isfinite(points).all():
            raise ValueError("nonfinite fitted coordinates")
        return " ".join(f"{float(x):.17g},{float(y):.17g}" for x, y in points)

    for p in rings:
        before += len(p)
        cuts, corners, incoming, outgoing, tangents = _features(p, tolerance, corner_angle)
        parts.append("M" + coords(p[cuts[0]:cuts[0]+1]))
        for i, first in enumerate(cuts):
            last = cuts[(i + 1) % len(cuts)]
            indices = np.arange(first, last + (len(p) if last <= first else 0) + 1) % len(p)
            commands, hit_cap = _fit(p[indices], tangents[indices],
                outgoing[first] if first in corners else tangents[first],
                incoming[last] if last in corners else tangents[last], tolerance)
            capped |= hit_cap
            for command, points in commands:
                parts.append(command + coords(points))
                after += 1
                cubics += command == "C"
        parts.append("Z")
    limitations = [
        "Tolerance bounds sampled vertex residuals only; between-vertex deviation is not certified.",
        "Ring order/closure are retained, but intersections, winding and hole topology are not certified; render and compare before accepting.",
        "Neighborhood corner detection and smooth seam tangents are heuristic; tiny or undersampled features may change.",
    ]
    if capped:
        limitations.append(f"Recursion cap {_MAX_DEPTH} reached; remaining spans retained as lines.")
    return " ".join(parts), dict(segments_before=before, segments_after=after,
        cubic_segments=cubics, tolerance=float(tolerance), limitations=limitations)

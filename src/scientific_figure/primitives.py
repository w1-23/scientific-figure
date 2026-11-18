"""Small deterministic authoring helpers and explicitly experimental outlines."""
from __future__ import annotations
import hashlib
import math
from .scene import number


def namespaced_id(instance, part, kind='o'):
    """Stable bounded IDs across instances, objects, resources and relations."""
    if not isinstance(instance, str) or not 1 <= len(instance) <= 1000:
        raise ValueError('instance requires 1..1000 characters')
    if not isinstance(part, str) or not 1 <= len(part) <= 1000 or kind not in {'o', 'r', 'x'}:
        raise ValueError('invalid namespace part/kind')
    prefix = hashlib.sha256(instance.encode()).hexdigest()[:24]
    suffix = hashlib.sha256(part.encode()).hexdigest()[:24]
    return f'n_{prefix}_{kind}_{suffix}'


def affine(*, x=0, y=0, angle=0, sx=1, sy=None):
    """Local scale, then rotation in degrees, then translation."""
    sy = sx if sy is None else sy
    for value in (x, y, angle, sx, sy): number(value, 'affine parameter')
    if abs(sx * sy) < 1e-10: raise ValueError('singular scale')
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return [c*sx, s*sx, -s*sy, c*sy, x, y]


def ellipse_path(cx, cy, rx, ry=None):
    ry = rx if ry is None else ry
    for value in (cx, cy): number(value, 'ellipse coordinate')
    for value in (rx, ry): number(value, 'ellipse radius', positive=True)
    return f'M{cx-rx} {cy}A{rx} {ry} 0 1 0 {cx+rx} {cy}A{rx} {ry} 0 1 0 {cx-rx} {cy}Z'


def normalize_fill(d, *, fill_rule='nonzero', tolerance=.25):
    """Resolve fill winding/crossings, not arbitrary curve reduction or smoothing."""
    from .pathops_backend import _backend, _path, _svg, _tolerance
    if fill_rule not in {'nonzero', 'evenodd'}: raise ValueError('invalid fill rule')
    _tolerance(tolerance)
    path = _path(d, _backend(), closed=True, fill_rule=fill_rule)
    path.simplify()
    return _svg(path, tolerance)


def experimental_closed_offset(d, distance, *, fill_rule='nonzero', tolerance=.25):
    """Round boundary-band dilation/erosion candidate; topology may change."""
    from .pathops_backend import boolean, stroke_outline, _recording
    number(distance, 'offset distance')
    original = normalize_fill(d, fill_rule=fill_rule, tolerance=tolerance)
    if not original or distance == 0:
        result = original
    else:
        band = stroke_outline(original, 2*abs(distance), tolerance=tolerance)
        result = boolean('union' if distance > 0 else 'difference', original, band, tolerance=tolerance)
    contours = lambda path: sum(op == 'moveTo' for op, _ in _recording(path).value)
    before, after = contours(original), contours(result)
    return {'status': 'experimental', 'd': result, 'collapsed': not bool(result),
            'contours_before': before, 'contours_after': after,
            'contour_count_changed': before != after,
            'limitations': ['Round filled-boundary offset only; not a general CAD offset.',
                            'Matching contour counts do not prove preserved topology.',
                            'Float32 and arc/conic approximations; no global error bound.']}


def experimental_variable_outline(points, widths, *, tolerance=.25):
    """Union disks and trapezoids for explicit sampled centerline/width inputs.

    Round sample joins and caps. No curve inference or true-width recovery.
    """
    from .pathops_backend import boolean
    if not isinstance(points, list) or not isinstance(widths, list) or not 2 <= len(points) <= 128 or len(points) != len(widths):
        raise ValueError('requires 2..128 points with matching widths')
    for xy, width in zip(points, widths):
        if not isinstance(xy, list) or len(xy) != 2: raise ValueError('point requires two coordinates')
        for value in xy: number(value, 'centerline coordinate')
        number(width, 'sample width', positive=True)
    result = ''
    for xy, width in zip(points, widths):
        result = boolean('union', result, ellipse_path(*xy, width/2), tolerance=tolerance)
    for a, b, wa, wb in zip(points, points[1:], widths, widths[1:]):
        dx, dy = b[0]-a[0], b[1]-a[1]
        length = math.hypot(dx, dy)
        if length < 1e-8: raise ValueError('duplicate consecutive sample')
        nx, ny = -dy/length, dx/length
        corners = [(a[0]+nx*wa/2, a[1]+ny*wa/2), (b[0]+nx*wb/2, b[1]+ny*wb/2),
                   (b[0]-nx*wb/2, b[1]-ny*wb/2), (a[0]-nx*wa/2, a[1]-ny*wa/2)]
        polygon = 'M' + 'L'.join(f'{x} {y}' for x, y in corners) + 'Z'
        result = boolean('union', result, polygon, tolerance=tolerance)
    return {'status': 'experimental', 'd': result,
            'limitations': ['Sampled disk/trapezoid envelope; smooth continuous offset not certified.',
                            'Self overlaps are unioned; scientific topology requires host review.']}

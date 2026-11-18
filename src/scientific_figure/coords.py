"""One coordinate contract for reference reconstruction.

Source pixels (EXIF-oriented, pixel boundaries) -> ROI local -> module local ->
Scene -> SVG. Matrices use SVG order ``[a, b, c, d, e, f]``. Every raster entry
in this package loads through :func:`load_oriented`, so EXIF rotation is applied
once, before any ROI or edge work.

Nothing here interprets content; it only maps coordinates deterministically.
"""
from __future__ import annotations

from io import BytesIO
import math
from pathlib import Path

from PIL import Image, ImageOps

from .geometry import inverse, multiply

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_INPUT_PIXELS = 40_000_000


def _read_bounded(path, limit=MAX_INPUT_BYTES):
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"input exceeds {limit} byte budget")
    return data


def load_oriented(path, *, region=None, background=None, limit_bytes=MAX_INPUT_BYTES):
    """Decode one single-frame image in EXIF-oriented coordinates.

    Returns ``(rgb_image, metadata)``. ``region`` is integer
    ``[x, y, width, height]`` in oriented pixel boundaries. A partially
    transparent crop requires an explicit background color; it is composited
    before any analysis so later steps cannot treat alpha as a feature.
    """
    data = _read_bounded(path, limit_bytes)
    with Image.open(BytesIO(data)) as raw:
        raw_size = list(raw.size)
        if raw.width * raw.height > MAX_INPUT_PIXELS:
            raise ValueError("source exceeds input pixel budget")
        if getattr(raw, "n_frames", 1) != 1:
            raise ValueError("multi-frame images require an explicit single-frame source")
        orientation = raw.getexif().get(274, 1)
        if type(orientation) is not int or orientation not in range(1, 9):
            raise ValueError("invalid EXIF orientation")
        oriented = ImageOps.exif_transpose(raw)
        size = list(oriented.size)
        roi = [0, 0, size[0], size[1]] if region is None else list(region)
        if (len(roi) != 4 or any(type(v) is not int for v in roi)
                or min(roi[2:]) <= 0 or min(roi[:2]) < 0
                or roi[0] + roi[2] > size[0] or roi[1] + roi[3] > size[1]):
            raise ValueError("ROI must be integer [x, y, width, height] inside oriented source")
        x, y, w, h = roi
        cropped = oriented.crop((x, y, x + w, y + h)).convert("RGBA")
        has_alpha = cropped.getchannel("A").getextrema()[0] < 255
        if has_alpha and background is None:
            raise ValueError("transparent ROI requires an explicit alpha background")
        if background is not None:
            base = Image.new("RGBA", cropped.size, background)
            cropped = Image.alpha_composite(base, cropped)
        rgb = cropped.convert("RGB")
        rgb.info.clear()
    return rgb, {"path": str(Path(path).resolve()), "raw_size": raw_size, "size": size,
                 "exif_orientation": orientation, "roi": roi,
                 "coordinate_space": "exif-oriented-pixel-boundaries",
                 "alpha_composited": has_alpha, "background": background,
                 "bytes": len(data)}


def apply(matrix, x, y):
    """Map one point through an SVG-order affine matrix."""
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def source_to_roi(roi):
    """Translation from source pixels to ROI-local pixels."""
    return [1, 0, 0, 1, -roi[0], -roi[1]]


def roi_to_scene(canvas, roi, *, mode="contain", background="none"):
    """Uniform placement of the ROI rectangle inside a Scene canvas.

    ``contain`` preserves aspect ratio and centers the image (letterboxed by the
    canvas background). ``stretch`` is explicit and never implicit.
    """
    width, height = canvas["width"], canvas["height"]
    rw, rh = roi[2], roi[3]
    if mode == "stretch":
        sx, sy = width / rw, height / rh
        return [sx, 0, 0, sy, 0, 0]
    if mode != "contain":
        raise ValueError("unknown scene fit mode")
    scale = min(width / rw, height / rh)
    ox, oy = (width - rw * scale) / 2, (height - rh * scale) / 2
    return [scale, 0, 0, scale, ox, oy]


def source_to_scene(canvas, roi, *, mode="contain"):
    return multiply(roi_to_scene(canvas, roi, mode=mode), source_to_roi(roi))


def scene_to_svg(scene):
    """The Scene canvas is the SVG user space; only ``viewBox`` reframes it."""
    box = scene["canvas"].get("viewBox")
    if not box:
        return [1, 0, 0, 1, 0, 0]
    return [1, 0, 0, 1, -box[0], -box[1]]


def frame_matrix(frame):
    """Module-local -> Scene transform from an explicit local frame.

    ``frame`` = ``{"origin": [x, y], "scale": s, "rotation": degrees}``.
    Rotation is clockwise on screen (positive y down), matching SVG.
    """
    if not isinstance(frame, dict) or set(frame) - {"origin", "scale", "rotation"}:
        raise ValueError("local frame accepts origin, scale and rotation only")
    origin = frame.get("origin", [0, 0])
    scale = frame.get("scale", 1)
    rotation = frame.get("rotation", 0)
    if (not isinstance(origin, (list, tuple)) or len(origin) != 2
            or any(type(v) not in {int, float} or not math.isfinite(v) for v in origin)):
        raise ValueError("local frame origin must be two finite numbers")
    if type(scale) not in {int, float} or not math.isfinite(scale) or scale <= 0:
        raise ValueError("local frame scale must be a positive finite number")
    if type(rotation) not in {int, float} or not math.isfinite(rotation):
        raise ValueError("local frame rotation must be a finite number")
    angle = math.radians(rotation)
    cos, sin = math.cos(angle), math.sin(angle)
    a, b = scale * cos, scale * sin
    c, d = -scale * sin, scale * cos
    return [a, b, c, d, origin[0], origin[1]]


def module_world(scene_geometry, module_id):
    """World matrix of a module group, or identity for a missing group."""
    return scene_geometry.world(module_id) if module_id in scene_geometry.nodes else [1, 0, 0, 1, 0, 0]


def invert(matrix):
    return inverse(matrix)

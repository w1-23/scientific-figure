"""Trace cropped raster regions into editable, region-local SVG polygons."""
from pathlib import Path
import math
import re

import cv2
import numpy as np
from PIL import Image


def trace_region(
    image_path: Path, region: list[int], *, object_id: str,
    palette: list[str] | None = None, hsv: list[list[int]] | None = None,
    colors: int = 6, min_area: float = 3.0, simplify: float = 0.7,
    max_paths: int = 200,
) -> tuple[list[dict], dict]:
    """Return path nodes and diagnostics; the caller owns the parent group.

    HSV takes precedence over RGB assignment when supplied. Coordinates follow
    OpenCV pixel centers; quantization and simplification are approximations.
    Fully transparent pixels are excluded; partial alpha is treated as opaque.
    min_area filters components by net contour area, never individual holes.
    """
    if (not isinstance(region, list) or len(region) != 4
            or any(type(v) is not int for v in region)):
        raise ValueError("region must be [left, top, width, height] integers")
    left, top, width, height = region
    if left < 0 or top < 0 or width <= 0 or height <= 0:
        raise ValueError("region origin must be nonnegative and dimensions positive")
    if not isinstance(object_id, str) or not object_id.strip():
        raise ValueError("object_id must be a nonempty string")
    if type(colors) is not int or not 1 <= colors <= 256:
        raise ValueError("colors must be an integer in [1, 256]")
    if type(max_paths) is not int or max_paths <= 0:
        raise ValueError("max_paths must be a positive integer")
    for name, value in (("min_area", min_area), ("simplify", simplify)):
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or value < 0):
            raise ValueError(f"{name} must be finite and nonnegative")
    hexes = []
    if palette is not None:
        if (not isinstance(palette, list) or not palette
                or any(not isinstance(c, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", c)
                       for c in palette)):
            raise ValueError("palette must contain #rrggbb colors")
        hexes = [c.lower() for c in palette]
    if hsv is not None:
        if (not isinstance(hsv, list) or len(hsv) != 2
                or any(not isinstance(row, list) or len(row) != 3 for row in hsv)):
            raise ValueError("hsv must contain two three-channel bounds")
        if any(type(v) is not int for row in hsv for v in row):
            raise ValueError("hsv bounds must be integers")
        if any(not 0 <= hsv[0][i] <= hsv[1][i] <= cap
               for i, cap in enumerate((179, 255, 255))):
            raise ValueError("hsv bounds must be ordered within OpenCV ranges")
    with Image.open(image_path) as source:
        if left + width > source.width or top + height > source.height:
            raise ValueError("region extends beyond the image")
        rgba = np.asarray(source.convert("RGBA").crop(
            (left, top, left + width, top + height)))
    rgb, visible = rgba[:, :, :3], rgba[:, :, 3] > 0
    if not visible.any():
        raise ValueError("no contours: region has no visible pixels")
    masks = []
    if hsv is not None:
        converted = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2HSV)
        mask = (cv2.inRange(converted, np.array(hsv[0], dtype=np.uint8),
                            np.array(hsv[1], dtype=np.uint8)) > 0) & visible
        if not mask.any():
            raise ValueError("no contours: HSV selection is empty")
        fill = hexes[-1] if hexes else _hex(np.median(rgb[mask], axis=0).astype(int))
        hexes = hexes or [fill]
        masks.append((fill, mask))
    elif palette is not None:
        pixels = rgb.astype(np.int32)
        labels = np.zeros(visible.shape, dtype=np.int32)
        best = np.full(visible.shape, np.iinfo(np.int32).max, dtype=np.int32)
        for i, color in enumerate(hexes):
            distance = ((pixels - _rgb(color)) ** 2).sum(axis=2)
            closer = distance < best
            labels[closer], best[closer] = i, distance[closer]
        masks = [(c, (labels == i) & visible) for i, c in enumerate(hexes) if i]
    else:
        # Train only on visible pixels so hidden RGB cannot consume color slots.
        sample = Image.fromarray(rgb[visible][None, :, :])
        quantized = sample.quantize(colors=colors, method=Image.Quantize.FASTOCTREE,
                                   dither=Image.Dither.NONE)
        labels = np.full(visible.shape, -1, dtype=np.int32)
        labels[visible] = np.asarray(quantized).ravel()
        table = np.array(quantized.getpalette(), dtype=np.uint8).reshape(-1, 3)
        used = np.unique(labels[visible])
        hexes = [_hex(table[i]) for i in used]
        boundary = np.zeros(visible.shape, dtype=bool)
        boundary[[0, -1], :] = True
        boundary[:, [0, -1]] = True
        edge_labels = labels[boundary & visible]
        background = (int(np.bincount(edge_labels).argmax())
                      if edge_labels.size else -1)
        masks = [(_hex(table[i]), labels == i) for i in used if i != background]
    components, mask_pixel_count = [], 0
    for fill, mask in masks:
        mask_pixel_count += int(np.count_nonzero(mask))
        contours, hierarchy = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP,
                                              cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            continue
        hierarchy = hierarchy[0]
        for i, contour in enumerate(contours):
            if hierarchy[i][3] != -1:
                continue
            rings = [contour]
            child = hierarchy[i][2]
            while child != -1:
                rings.append(contours[child])
                child = hierarchy[child][0]
            area = abs(cv2.contourArea(contour)) - sum(
                abs(cv2.contourArea(hole)) for hole in rings[1:])
            if area < min_area or area <= 0:
                continue
            # Keep every hole, including those smaller than min_area.
            parts, vertices = [], 0
            for ring in rings:
                points = cv2.approxPolyDP(ring, simplify, True).reshape(-1, 2)
                if len(points) < 3 or cv2.contourArea(points) == 0:
                    points = ring.reshape(-1, 2)
                if len(points) < 3:
                    continue
                vertices += len(points)
                parts.append("M" + "L".join(f"{int(x)},{int(y)}" for x, y in points) + "Z")
            if parts:
                x, y, _, _ = cv2.boundingRect(contour)
                components.append((-area, y, x, fill, "".join(parts), vertices))
    if not components:
        raise ValueError("no contours after background removal and area filtering")
    if len(components) > max_paths:
        raise ValueError(f"path budget exceeded: {len(components)} > max_paths={max_paths}")
    components.sort()
    nodes = []
    for number, (_, _, _, fill, d, _) in enumerate(components, 1):
        nodes.append({
            "id": f"{object_id}_p{number:04d}", "kind": "path", "parent": object_id,
            "geometry": {"d": d}, "style": {"fill": fill, "fill_rule": "evenodd"},
            "semantic": {"role": "contour", "name": f"{object_id} contour {number:04d}"},
        })
    return nodes, {
        "backend": "opencv-contours", "region": region.copy(), "palette": hexes,
        "path_count": len(nodes), "vertices": sum(c[-1] for c in components),
        "mask_pixel_count": mask_pixel_count,
        "limitations": [
            "Color quantization/nearest-palette assignment may lose color detail.",
            "Partial alpha is treated as opaque; semitransparency is not preserved.",
            "Contours are geometric, not semantic object recognition.",
            "Pixel-center contours, area filtering and simplification approximate raster edges.",
        ],
    }


def _rgb(color: str) -> np.ndarray:
    return np.array([int(color[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.int32)


def _hex(color) -> str:
    return "#" + "".join(f"{int(v):02x}" for v in color)

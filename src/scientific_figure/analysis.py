"""Source-bound visual evidence. Scientific identities are supplied by the host.

Color regions are observations, never automatically scientific objects. No mask
from a generated candidate is used as the reference for candidate evaluation.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .quality import topology
from .scene import schema_validate


def source_crop(path, region=None):
    path = Path(path)
    with Image.open(path) as image:
        size = list(image.size)
        region = region or [0, 0, *size]
        if (not isinstance(region, list) or len(region) != 4
                or any(type(v) is not int for v in region)):
            raise ValueError("region requires four integers")
        x, y, w, h = region
        if min(x, y) < 0 or min(w, h) <= 0 or x+w > size[0] or y+h > size[1]:
            raise ValueError("region outside source")
        if w*h > 512*512:
            raise ValueError("unsupported: adaptive analysis exceeds 512x512 ROI budget")
        pixels = np.asarray(image.convert("RGBA").crop((x, y, x+w, y+h))).copy()
    return pixels, {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "size": size, "region": region}


def rgb(color):
    if (not isinstance(color, str) or len(color) != 7 or not color.startswith("#")):
        raise ValueError("colors require #rrggbb")
    try:
        return np.array([int(color[i:i+2], 16) for i in (1, 3, 5)], dtype=np.uint8)
    except ValueError:
        raise ValueError("invalid RGB color") from None


def hex_color(values):
    return "#" + "".join(f"{int(v):02x}" for v in values)


def foreground(pixels, background, threshold=14):
    # Alpha is direct evidence when transparency is actually present.
    alpha = pixels[:, :, 3]
    if np.any(alpha < 128):
        return alpha >= 128
    return np.max(np.abs(pixels[:, :, :3].astype(float)-rgb(background)), axis=2) > threshold


def palette_masks(pixels, palette, background, active=None):
    colors = np.array([rgb(c) for c in palette], dtype=float)
    distances = ((pixels[:, :, None, :3].astype(float)-colors)**2).sum(axis=3)
    labels = distances.argmin(axis=2)
    active = foreground(pixels, background) if active is None else active
    return [((labels == i) & active) for i in range(len(palette))]


def analyze(path, region=None, *, colors=6, background=None, parts=None, evidence=None):
    """Default: the unchanged, persistable Analysis 0.2 document.

    evidence=["contrast"] requests only RGB edge evidence and returns a standalone,
    non-persistable evidence result. It cannot be passed to bind_analysis/search.
    No palette, topology, dark-ridge extraction or skeleton graph is computed.
    """
    if evidence is not None and evidence != ["contrast"]:
        raise ValueError('explicit evidence currently supports only ["contrast"]')
    if evidence is not None and parts is not None:
        raise ValueError("contrast-only evidence does not accept host parts")
    if type(colors) is not int or not 1 <= colors <= 16:
        raise ValueError("analysis colors must be in [1,16]")
    pixels, source = source_crop(path, region)
    edges = np.concatenate([pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]])
    visible_edges = edges[edges[:, 3] >= 128, :3]
    inferred = hex_color(np.median(visible_edges, axis=0)) if len(visible_edges) else "#ffffff"
    host_background=background is not None
    background = background or inferred
    rgb(background)
    active = foreground(pixels, background)
    if evidence is not None:
        from .internal_structure import contrast_summary
        return {"kind": "evidence_result", "evidence": ["contrast"], "persistent": False,
                "source": source, "background": background,
                "source_to_local": [1, 0, 0, 1, -source["region"][0], -source["region"][1]],
                "contrast": contrast_summary(pixels, active, background),
                "configuration": {"foreground_threshold": 14,
                                  "background_evidence": "host_provided" if host_background else "boundary_median_hypothesis"}}
    if not active.any():
        raise ValueError("needs_input: no foreground under selected background hypothesis")
    # Quantize source foreground only; do not spend slots on the background.
    sample = Image.fromarray(pixels[:, :, :3][active][None, :, :])
    quantized = sample.quantize(colors=colors, method=Image.Quantize.FASTOCTREE,
                                dither=Image.Dither.NONE)
    table = np.array(quantized.getpalette(), dtype=np.uint8).reshape(-1, 3)
    palette = [hex_color(table[i]) for i in sorted(np.unique(np.asarray(quantized)).tolist())]
    masks = palette_masks(pixels, palette, background)
    regions = []
    for i, (color, mask) in enumerate(zip(palette, masks, strict=True)):
        yy, xx = np.nonzero(mask)
        if not len(xx):
            continue
        regions.append({"id": f"region_{i+1}", "color": color, "pixel_count": int(mask.sum()),
                        "bbox": [int(xx.min()), int(yy.min()), int(xx.max()+1), int(yy.max()+1)],
                        "topology": topology(mask), "boundary": "closed_region",
                        "evidence": "observed_color_partition", "scientific_identity": "unknown"})
    parts = parts or []
    if not isinstance(parts, list):
        raise ValueError("parts must be an array")
    ids = set()
    for part in parts:
        if (not isinstance(part, dict) or set(part)-{"id", "color", "role", "required"}
                or not {"id", "color"} <= set(part) or part["id"] in ids):
            raise ValueError("invalid or duplicate host part")
        from .scene import ID
        if not isinstance(part["id"], str) or not ID.fullmatch(part["id"]):
            raise ValueError("invalid host part ID")
        rgb(part["color"])
        if "required" in part and not isinstance(part["required"], bool):
            raise ValueError("part required must be boolean")
        ids.add(part["id"])
    count, holes = topology(active)
    from .internal_structure import structure_summary
    structure = structure_summary(pixels, active, background)
    result = {"schema_version": "0.2", "source": source,
              "source_to_local": [1, 0, 0, 1, -source["region"][0], -source["region"][1]],
              "background": background, "palette": palette, "regions": regions, "parts": parts,
              "features": {"foreground_pixels": int(active.sum()), "components": count, "holes": holes,
                           "partial_alpha": bool(np.any((pixels[:, :, 3] > 0) & (pixels[:, :, 3] < 255)))},
              "relations": [], "internal_structure": structure,
              "uncertainties": ["Color partitions are not scientific objects.",
                                "Open/closed stroke roles, containment and occlusion need host evidence.",
                                "Hidden shape, original layers and alpha are not uniquely recoverable."],
              "configuration": {"colors": colors, "foreground_threshold": 14,
                                "background_evidence": "host_provided" if host_background else "boundary_median_hypothesis"}}
    schema_validate(result, "analysis")
    return result


def bind_analysis(path, analysis):
    schema_validate(analysis, "analysis")
    pixels, source = source_crop(path, analysis["source"]["region"])
    if source != analysis["source"]:
        raise ValueError("analysis source hash/size mismatch")
    return pixels

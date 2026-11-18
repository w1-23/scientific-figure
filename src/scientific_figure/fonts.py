"""Explicit local font selection, measurement and glyph checks."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from functools import lru_cache

from fontTools.ttLib import TTFont
from PIL import ImageFont


@lru_cache(maxsize=1)
def font_info() -> dict:
    configured = os.environ.get("SCIENTIFIC_FIGURE_FONT")
    candidates = [Path(configured)] if configured else [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    ]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        raise ValueError("needs_input: set SCIENTIFIC_FIGURE_FONT to a local TTF/OTF file")
    with TTFont(path) as font:
        family = font["name"].getDebugName(1)
        glyphs = set(font.getBestCmap() or {})
    return {"path": str(path.resolve()), "family": family,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "glyphs": glyphs}


def check_text(text: str, family: str | None = None) -> None:
    info = font_info()
    if family and family != info["family"]:
        raise ValueError(f"unsupported font family {family!r}; selected font is {info['family']!r}")
    missing = sorted({c for c in text if not c.isspace() and ord(c) not in info["glyphs"]})
    if missing:
        raise ValueError(f"needs_input: selected font has no glyphs for {''.join(missing)!r}")


def text_bounds(geometry: dict, style: dict) -> tuple[float, float, float, float]:
    size = style.get("font_size", 14)
    # Measure at 8x to reduce rounding of fractional font sizes.
    scale = 8
    font = ImageFont.truetype(font_info()["path"], max(1, round(size * scale)))
    x, y = geometry["x"], geometry["y"]
    boxes = []
    for i, line in enumerate(geometry["text"].split("\n")):
        left, top, right, bottom = font.getbbox(line or " ", anchor="ls")
        shift = {"start": 0, "middle": font.getlength(line) / 2,
                 "end": font.getlength(line)}[style.get("anchor", "start")]
        baseline = y + i * size * 1.2
        boxes.append((x + (left - shift) / scale, baseline + top / scale,
                      x + (right - shift) / scale, baseline + bottom / scale))
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))

"""Explicit local font selection, measurement, glyph checks and task probes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from functools import lru_cache

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont


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


FONT_SUFFIXES = {".ttf", ".otf", ".ttc", ".otc"}
FONT_DIRECTORIES = (
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
    Path("/System/Library/Fonts"), Path("/Library/Fonts"),
    Path.home() / ".fonts", Path.home() / ".local/share/fonts",
)
FONT_PROBE_LIMITS = {"texts": 200, "characters": 4096, "fonts": 64, "faces": 32,
                     "text_length": 20000, "missing_reported": 64}


def _font_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def font_candidates(directories=None, limit=FONT_PROBE_LIMITS["fonts"]):
    """Enumerate local font files in a deterministic, bounded order."""
    roots = [Path(item) for item in (FONT_DIRECTORIES if directories is None else directories)]
    found, seen = [], set()
    for root in roots:
        if not root.is_dir():
            continue
        for directory, subdirectories, files in os.walk(root):
            depth = len(Path(directory).relative_to(root).parts)
            if depth >= 3:
                subdirectories[:] = []
            subdirectories.sort()
            for name in sorted(files):
                if Path(name).suffix.lower() not in FONT_SUFFIXES:
                    continue
                path = Path(directory) / name
                key = str(path.resolve()).lower()
                if key in seen:
                    continue
                seen.add(key)
                found.append(path)
                if len(found) >= limit:
                    return found
    return found


def _font_faces(path, limit=FONT_PROBE_LIMITS["faces"]):
    """Read each face of a TTF/OTF/TTC without claiming fallback coverage."""
    try:
        with TTFont(path) as font:
            count = getattr(getattr(font, "reader", None), "numFonts", 1) or 1
    except Exception:
        count = 1
    faces = []
    for index in range(min(int(count), limit)):
        try:
            with TTFont(path, fontNumber=index) as font:
                name = font["name"].getDebugName(1) if "name" in font else None
                faces.append({"index": index, "family": name or Path(path).stem,
                              "glyphs": set(font.getBestCmap() or {})})
        except Exception as error:
            faces.append({"index": index, "error": f"{type(error).__name__}: {error}"[:200]})
    return faces


def probe_fonts(texts, *, candidates=None, font_size=14,
                limit=FONT_PROBE_LIMITS["fonts"]):
    """Check that one real local font covers every character of a task's text.

    The engine renders a single font family, so this reports the active font and
    per-face coverage rather than promising mixed-font fallback. It never
    changes the selected font or the environment.
    """
    if not isinstance(texts, list) or not 1 <= len(texts) <= FONT_PROBE_LIMITS["texts"]:
        raise ValueError("texts must be a nonempty list of at most "
                         + str(FONT_PROBE_LIMITS["texts"]) + " strings")
    for text in texts:
        if not isinstance(text, str) or not 1 <= len(text) <= FONT_PROBE_LIMITS["text_length"]:
            raise ValueError("each text must be a string of 1..%d characters"
                             % FONT_PROBE_LIMITS["text_length"])
    characters = sorted({character for text in texts for character in text if not character.isspace()})
    if not characters:
        raise ValueError("texts contain no drawable characters")
    if len(characters) > FONT_PROBE_LIMITS["characters"]:
        raise ValueError("texts exceed the character budget; probe one page at a time")

    active = None
    try:
        info = font_info()
        missing = [character for character in characters if ord(character) not in info["glyphs"]]
        active = {"path": info["path"], "family": info["family"], "sha256": info["sha256"],
                  "missing_characters": missing, "missing_count": len(missing),
                  "covers_all": not missing, "activation": "in_use"}
    except (ValueError, OSError):
        pass

    paths = []
    for item in (candidates or []):
        if not isinstance(item, (str, os.PathLike)):
            raise ValueError("each candidate font must be a path")
        paths.append(Path(item))
    if not candidates:
        paths = [Path(active["path"])] if active else []
        known = {str(path.resolve()).lower() for path in paths}
        paths += [path for path in font_candidates(limit=limit)
                  if str(path.resolve()).lower() not in known]
    inspected, seen = [], set()
    for path in paths[:limit]:
        resolved = str(path.resolve())
        if resolved.lower() in seen:
            continue
        seen.add(resolved.lower())
        entry = {"path": resolved, "exists": path.is_file()}
        if not entry["exists"]:
            entry["error"] = "missing file"
            inspected.append(entry)
            continue
        try:
            entry["bytes"] = path.stat().st_size
            entry["sha256"] = _font_sha256(path)
            faces = _font_faces(path)
        except Exception as error:
            entry["error"] = f"{type(error).__name__}: {error}"[:200]
            inspected.append(entry)
            continue
        public = []
        for face in faces:
            if "error" in face:
                public.append(face)
                continue
            missing = [character for character in characters if ord(character) not in face["glyphs"]]
            public.append({"index": face["index"], "family": face["family"],
                           "glyphs": len(face["glyphs"]), "covers_all": not missing,
                           "missing_count": len(missing),
                           "missing_characters": missing[:FONT_PROBE_LIMITS["missing_reported"]]})
        entry["faces"] = public
        entry["covers_all"] = any(face.get("covers_all") for face in public)
        entry["selectable"] = any(face.get("covers_all") and face["index"] == 0 for face in public)
        inspected.append(entry)

    selected = None
    if active and active["covers_all"]:
        selected = {"path": active["path"], "family": active["family"],
                    "sha256": active["sha256"], "face_index": 0, "activation": "active_font"}
    else:
        usable = next((entry for entry in inspected if entry.get("selectable")), None)
        if usable is not None:
            face = next(face for face in usable["faces"]
                        if face.get("covers_all") and face["index"] == 0)
            selected = {"path": usable["path"], "family": face["family"], "sha256": usable["sha256"],
                        "face_index": 0,
                        "activation": "set SCIENTIFIC_FIGURE_FONT to this path"}
    missing = [] if selected else (active["missing_characters"] if active else characters)
    return {"schema_version": "0.1", "kind": "font_probe",
            "status": "ok" if selected else "needs_input",
            "texts": {"count": len(texts), "unique_characters": len(characters),
                      "total_characters": sum(len(text) for text in texts),
                      "sha256": hashlib.sha256(json.dumps(texts, ensure_ascii=False).encode("utf-8")).hexdigest()},
            "font_size": float(font_size),
            "active_font": active, "selected": selected,
            "missing_characters": missing[:FONT_PROBE_LIMITS["missing_reported"]],
            "missing_count": len(missing),
            "candidates": inspected, "candidates_inspected": len(inspected),
            "single_font_rendering": True, "mixed_font_layout": "unsupported",
            "limitations": [
                "Checks one installed font family at a time; the engine does not compose fallback fonts.",
                "Coverage means a glyph exists, not that its shape or metrics suit the figure.",
                "No font is installed or downloaded and SCIENTIFIC_FIGURE_FONT is not changed.",
                "Only TTC face 0 can be selected through SCIENTIFIC_FIGURE_FONT; other faces are reported only."]}


def write_font_sample(texts, output, *, font_path=None, font_size=14, max_lines=50):
    """Draw a labelled sample using the selected (or given) font only."""
    path = Path(font_path) if font_path else Path(font_info()["path"])
    size = max(1, round(float(font_size)))
    font = ImageFont.truetype(str(path), size)
    glyphs = _font_faces(path, limit=1)[0].get("glyphs") or set()
    sample = ["ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz",
              "0123456789 .,;:!?()[]{}%+-*/=<>≤≥±×÷°µ",
              "αβγδεθλμσφωΩΔΣ ∑ ∫ √ ∞ → ← ↑ ↓ ↔",
              "H₂O CO₂ O₂⁻ x² y₁ ΔT° 中文测试：细胞膜、线粒体",
              "The quick brown fox jumps over the lazy dog."]
    # A sample line whose characters are missing from the chosen font would
    # draw tofu and read as a failed probe; omit it instead.
    lines = [line for line in sample
             if all(character.isspace() or ord(character) in glyphs for character in line)]
    task_lines = [line for text in texts for line in text.splitlines() if line.strip()][:max_lines]
    lines += task_lines
    lines = [line if len(line) <= 90 else line[:87] + "..." for line in lines]
    width, margin = 960, 16
    line_height = round(size * 1.9)
    image = Image.new("RGB", (width, margin * 2 + line_height * len(lines)), "#ffffff")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((margin, margin + index * line_height), line, font=font, fill="#111111")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    return {"path": str(Path(output).resolve()), "size": [width, image.height],
            "lines": len(lines), "texts": len(texts), "text_lines_drawn": len(task_lines),
            "font": str(path.resolve()), "font_size": size}

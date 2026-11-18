"""Bounded, deterministic CPU raster evidence; never infers scientific identity.

ROI is [x, y, width, height] in EXIF-oriented source pixel-boundary coordinates.
Affine matrices use SVG order [a, b, c, d, e, f]. Board mappings address the
source panel, not the other diagnostic panels. Timestamps describe the run;
raster bytes and palette selection are deterministic for fixed library versions.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
import ctypes
import hashlib
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import tempfile

import cv2
import numpy as np
import PIL
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .geometry import inverse, multiply
from .scene import load_json, schema_validate

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_INPUT_PIXELS = 40_000_000
MAX_ROI_PIXELS = 1_000_000
MAX_BOARD_PIXELS = 2_000_000
MAX_DERIVED_PIXELS = 16_000_000
CHANNELS = ("contrast", "detail", "palette")
PARAMETERS = {
    "algorithm": "cpu-raster-evidence-v1",
    "contrast": {"sigma": 1.2, "canny_low": 40, "canny_high": 100},
    "detail": {"sigma": 0.0, "canny_low": 15, "canny_high": 40},
    "gradient": "sobel-3-l2-clipped-255",
    "palette": {"method": "lab-bin-population-farthest-v1", "bins_per_axis": 8,
                "max_colors": 8},
    "board": {"tile": 384, "label_height": 24, "columns": 3,
              "sampling": "nearest", "coordinates": "pixel-boundaries"},
    "limits": {"input_bytes": MAX_INPUT_BYTES, "input_pixels": MAX_INPUT_PIXELS,
               "roi_pixels": MAX_ROI_PIXELS, "board_pixels": MAX_BOARD_PIXELS,
               "derived_pixels": MAX_DERIVED_PIXELS},
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _write_json(path, value):
    import json
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")


def _read_bounded(path, limit):
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"input exceeds {limit} byte budget")
    return data


def _rename_new(stage, target):
    """Publish atomically without replacing an existing directory, even if empty."""
    if os.name == "nt":
        os.rename(stage, target)  # Windows rename never replaces an existing target.
        return
    if platform.system() == "Linux":
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise OSError("atomic no-replace directory publication needs renameat2")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                           ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(stage), -100, os.fsencode(target), 1):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(target))
        return
    raise OSError("atomic no-replace directory publication unsupported on this platform")


@contextmanager
def _staged_directory(output):
    target = Path(output).absolute()
    if os.path.lexists(target):
        raise FileExistsError(str(target))
    if not target.parent.is_dir():
        raise ValueError("output parent directory must exist")
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        yield stage, target
        _rename_new(stage, target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _channels(value):
    if value is None:
        return ["contrast"]
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("channels must be a nonempty list")
    if any(not isinstance(c, str) or c not in CHANNELS for c in value):
        raise ValueError("unsupported evidence channel")
    if len(set(value)) != len(value):
        raise ValueError("duplicate evidence channel")
    return [c for c in CHANNELS if c in value]


def _background(value):
    if value is None:
        return None
    if isinstance(value, str) and re.fullmatch(r"#[a-fA-F0-9]{6}", value):
        return value.lower()
    if isinstance(value, (list, tuple)) and len(value) == 3 and all(
            type(v) is int and 0 <= v <= 255 for v in value):
        return "#" + "".join(f"{v:02x}" for v in value)
    raise ValueError("background must be #RRGGBB or three integer RGB components")


def _region(value, size):
    width, height = size
    roi = [0, 0, width, height] if value is None else value
    if not isinstance(roi, (list, tuple)) or len(roi) != 4 or any(type(v) is not int for v in roi):
        raise ValueError("ROI must be integer [x, y, width, height]")
    x, y, w, h = roi
    if min(x, y) < 0 or min(w, h) <= 0 or x + w > width or y + h > height:
        raise ValueError("ROI outside EXIF-oriented source bounds")
    if w * h > MAX_ROI_PIXELS:
        raise ValueError("ROI exceeds pixel budget; supply a smaller region")
    return list(roi)


def _load_source(path, region, background):
    data = _read_bounded(path, MAX_INPUT_BYTES)
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
        roi = _region(region, oriented.size)
        x, y, w, h = roi
        cropped = oriented.crop((x, y, x + w, y + h)).convert("RGBA")
        has_alpha = cropped.getchannel("A").getextrema()[0] < 255
        if has_alpha and background is None:
            raise ValueError("transparent ROI requires an explicit alpha background")
        if background is not None:
            base = Image.new("RGBA", cropped.size, background)
            cropped = Image.alpha_composite(base, cropped)
        rgb = cropped.convert("RGB")
        # No source metadata (including EXIF) is copied into derived PNGs.
        rgb.info.clear()
        source = {"path": str(Path(path).resolve()), "sha256": _hash(data),
                  "bytes": len(data), "raw_size": raw_size,
                  "size": list(oriented.size), "exif_orientation": orientation,
                  "coordinate_space": "exif-oriented-pixel-boundaries",
                  "roi": roi, "alpha_composited": has_alpha, "background": background}
    return rgb, source


def _diagnostics(image, channel):
    rgb = np.asarray(image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    params = PARAMETERS[channel]
    if params["sigma"]:
        gray = cv2.GaussianBlur(gray, (5, 5), params["sigma"],
                                borderType=cv2.BORDER_REPLICATE)
    dx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3, borderType=cv2.BORDER_REPLICATE)
    dy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3, borderType=cv2.BORDER_REPLICATE)
    gradient = np.clip(np.hypot(dx, dy), 0, 255).astype(np.uint8)
    edges = cv2.Canny(gray, params["canny_low"], params["canny_high"], L2gradient=True)
    overlay = rgb.copy()
    overlay[edges != 0] = [255, 0, 128]
    return {f"{channel}_gradient.png": Image.fromarray(gradient),
            f"{channel}_edges.png": Image.fromarray(edges),
            f"{channel}_overlay.png": Image.fromarray(overlay)}


def _palette(image):
    """Fixed Lab bins, then weighted farthest-point selection; no random k-means."""
    rgb = np.asarray(image).reshape(-1, 3)
    lab = cv2.cvtColor(rgb.reshape(1, -1, 3), cv2.COLOR_RGB2LAB).reshape(-1, 3)
    bins = lab.astype(np.int32) // 32
    keys = bins[:, 0] * 64 + bins[:, 1] * 8 + bins[:, 2]
    unique, indices, counts = np.unique(keys, return_inverse=True, return_counts=True)
    sums = np.stack([np.bincount(indices, weights=rgb[:, axis], minlength=len(unique))
                     for axis in range(3)], axis=1)
    centers = np.floor(sums / counts[:, None] + 0.5).astype(np.uint8)
    labs = cv2.cvtColor(centers[None, :, :], cv2.COLOR_RGB2LAB)[0].astype(np.float64)
    chosen = [int(np.argmax(counts))]
    distances = np.full(len(unique), np.inf)
    for _ in range(1, min(PARAMETERS["palette"]["max_colors"], len(unique))):
        distances = np.minimum(distances, np.sum((labs - labs[chosen[-1]]) ** 2, axis=1))
        scores = distances * np.sqrt(counts)
        scores[chosen] = -1
        chosen.append(int(np.argmax(scores)))
    # Describe actual pixel populations by nearest selected Lab representative.
    distances = np.stack([np.sum((lab.astype(np.float64) - labs[i]) ** 2, axis=1)
                          for i in chosen], axis=1)
    populations = np.bincount(np.argmin(distances, axis=1), minlength=len(chosen))
    entries = [{"rgb": centers[i].tolist(), "hex": "#" + "".join(f"{v:02x}" for v in centers[i]),
                "pixels": int(populations[j]), "fraction": float(populations[j] / len(rgb))}
               for j, i in enumerate(chosen)]
    swatches = Image.new("RGB", (256, 64), "white")
    draw = ImageDraw.Draw(swatches)
    for j, entry in enumerate(entries):
        draw.rectangle((j * 256 // len(entries), 0, (j + 1) * 256 // len(entries) - 1, 63),
                       fill=tuple(entry["rgb"]))
    return entries, swatches


def _board(images):
    """Fit grid cells to actual thumbnails and label ink, without upscaling."""
    tile, label, columns = (PARAMETERS["board"][k] for k in ("tile", "label_height", "columns"))
    cols = min(columns, len(images))
    rows = math.ceil(len(images) / cols)
    font = ImageFont.load_default()
    cells = []
    col_widths, row_heights = [0] * cols, [0] * rows
    for i, (name, image) in enumerate(images.items()):
        scale = min(1.0, tile / image.width, tile / image.height)
        width, height = max(1, int(image.width * scale)), max(1, int(image.height * scale))
        left, top, right, bottom = font.getbbox(name)
        text_width, text_height = math.ceil(right - left), math.ceil(bottom - top)
        if text_width + 8 > tile:
            raise ValueError("board label exceeds tile width")
        label_height = max(label, text_height + 8)
        col_widths[i % cols] = max(col_widths[i % cols], width, text_width + 8)
        row_heights[i // cols] = max(row_heights[i // cols], height + label_height)
        cells.append((name, image, width, height, label_height, left, top))
    size = (sum(col_widths), sum(row_heights))
    if size[0] * size[1] > MAX_BOARD_PIXELS:
        raise ValueError("board exceeds pixel budget")
    board = Image.new("RGB", size, "#eeeeee")
    draw = ImageDraw.Draw(board)
    panels = []
    for i, (name, image, width, height, label_height, left, top) in enumerate(cells):
        x, y = sum(col_widths[:i % cols]), sum(row_heights[:i // cols])
        board.paste(image.resize((width, height), Image.Resampling.NEAREST).convert("RGB"),
                    (x, y + label_height))
        # Compensate font bearings so all visible label ink stays inside its cell.
        draw.text((x + 4 - left, y + 4 - top), name, fill="black", font=font)
        matrix = [width / image.width, 0, 0, height / image.height, x, y + label_height]
        panels.append({"artifact": name, "rect": [x, y + label_height, width, height],
                       "artifact_to_board": matrix, "board_to_artifact": inverse(matrix)})
    return board, panels


def _record(path, image=None):
    data = path.read_bytes()
    record = {"path": path.name, "sha256": _hash(data), "bytes": len(data)}
    if image is not None:
        record["size"] = list(image.size)
        record["mode"] = image.mode
    return record


def _build(input_path, stage, channels, region, background):
    image, source = _load_source(input_path, region, background)
    images = {"source.png": image}
    palette = []
    for channel in channels:
        if channel == "palette":
            palette, images["palette.png"] = _palette(image)
        else:
            images.update(_diagnostics(image, channel))
    board, panels = _board(images)
    images["evidence_board.png"] = board
    total = sum(im.width * im.height for im in images.values())
    if total > MAX_DERIVED_PIXELS:
        raise ValueError("derived images exceed total pixel budget")
    records = []
    for name, im in images.items():
        im.save(stage / name, format="PNG", optimize=False, compress_level=9)
        records.append(_record(stage / name, im))
    x, y, _, _ = source["roi"]
    source_to_local = [1, 0, 0, 1, -x, -y]
    local_to_board = panels[0]["artifact_to_board"]
    source_to_board = multiply(local_to_board, source_to_local)
    mappings = {"source_to_local": source_to_local, "local_to_source": inverse(source_to_local),
                "local_to_board": local_to_board, "board_to_local": inverse(local_to_board),
                "source_to_board": source_to_board, "board_to_source": inverse(source_to_board)}
    evidence = {"schema_version": "0.1", "kind": "raster_evidence", "created_at": _now(),
                "source": source, "channels": channels, "parameters": PARAMETERS,
                "versions": {"python": platform.python_version(), "numpy": np.__version__,
                             "pillow": PIL.__version__, "opencv": cv2.__version__},
                "mappings": mappings, "panels": panels, "palette": palette,
                "derived_pixels": total, "artifacts": records}
    schema_validate(evidence, "evidence")
    _write_json(stage / "evidence.json", evidence)
    manifest = {"schema_version": "0.1", "kind": "evidence_manifest",
                "created_at": evidence["created_at"], "source_sha256": source["sha256"],
                "roi": source["roi"], "channels": channels, "parameters": PARAMETERS,
                "versions": evidence["versions"],
                "artifacts": records + [_record(stage / "evidence.json")]}
    schema_validate(manifest, "evidence-manifest")
    _write_json(stage / "manifest.json", manifest)


def _receipt(directory, channels):
    root = Path(directory).resolve()
    return {"schema_version": "0.1", "valid": True, "directory": str(root),
            "evidence_path": str(root / "evidence.json"),
            "manifest_path": str(root / "manifest.json"),
            "source_path": str(root / "source.png"),
            "board_path": str(root / "evidence_board.png"), "channels": channels}


def generate_evidence(input_path, output, *, channels=None, region=None, background=None):
    """Write one new atomic evidence directory and return one JSON-serializable receipt."""
    channels, background = _channels(channels), _background(background)
    with _staged_directory(output) as (stage, target):
        _build(input_path, stage, channels, region, background)
        validate_evidence(stage)
    return _receipt(target, channels)


def _safe_artifact(directory, name):
    # Also reject Windows drives, ADS names, backslashes and symlinks on every OS.
    path = PurePosixPath(name)
    if (len(path.parts) != 1 or path.is_absolute() or path.name in {"", ".", ".."}
            or any(c in name for c in "\\:/") or name != path.name):
        raise ValueError("unsafe artifact reference path")
    resolved = directory / name
    if resolved.is_symlink() or not resolved.is_file():
        raise ValueError("missing or symlink artifact reference")
    return resolved


def _expected_names(channels):
    names = {"source.png", "evidence_board.png"}
    for channel in channels:
        names.update({"palette.png"} if channel == "palette" else
                     {f"{channel}_{kind}.png" for kind in ("gradient", "edges", "overlay")})
    return names


def _check_matrix(actual, expected):
    if not np.allclose(actual, expected, rtol=0, atol=1e-9):
        raise ValueError("inconsistent coordinate mapping")
    inverse(actual)  # Independently reject singular matrices.


def validate_evidence(directory):
    """Validate strict schemas, safe references, bytes, raster budgets and mappings.

    Validation is self-contained: the original source may have moved. Its recorded
    hash identifies provenance; this function does not claim authenticity of files
    whose metadata and hashes an attacker has deliberately rewritten together.
    """
    root = Path(directory).resolve()
    for name in ("evidence.json", "manifest.json"):
        _safe_artifact(root, name)
    evidence, manifest = (load_json(root / name) for name in ("evidence.json", "manifest.json"))
    schema_validate(evidence, "evidence")
    schema_validate(manifest, "evidence-manifest")
    channels = _channels(evidence["channels"])
    if channels != evidence["channels"]:
        raise ValueError("channels are not in canonical order")
    source = evidence["source"]
    if "path" not in source:
        raise ValueError("directory evidence requires its original source path")
    if source["bytes"] > MAX_INPUT_BYTES or math.prod(source["size"]) > MAX_INPUT_PIXELS:
        raise ValueError("source exceeds budget")
    if math.prod(source["raw_size"]) > MAX_INPUT_PIXELS:
        raise ValueError("raw source exceeds pixel budget")
    orientation = source["exif_orientation"]
    expected_size = source["raw_size"][::-1] if orientation >= 5 else source["raw_size"]
    if source["size"] != expected_size:
        raise ValueError("inconsistent EXIF source dimensions")
    roi = _region(source["roi"], source["size"])
    _background(source["background"])
    if source["alpha_composited"] and source["background"] is None:
        raise ValueError("alpha background missing")
    if evidence["parameters"] != PARAMETERS or manifest["parameters"] != PARAMETERS:
        raise ValueError("unsupported evidence parameters or limits")
    for key, value in {"created_at": evidence["created_at"], "source_sha256": source["sha256"],
                       "roi": roi, "channels": channels, "versions": evidence["versions"]}.items():
        if manifest[key] != value:
            raise ValueError(f"inconsistent manifest {key}")
    expected_names = _expected_names(channels)
    if {p.name for p in root.iterdir()} != expected_names | {"evidence.json", "manifest.json"}:
        raise ValueError("unexpected or missing evidence files")
    image_records = evidence["artifacts"]
    records = manifest["artifacts"]
    if len(image_records) != len(expected_names) or {r["path"] for r in image_records} != expected_names:
        raise ValueError("duplicate or unexpected evidence artifact references")
    if len(records) != len(expected_names) + 1 or {r["path"] for r in records} != expected_names | {"evidence.json"}:
        raise ValueError("duplicate or unexpected manifest artifact references")
    if [r for r in records if r["path"] != "evidence.json"] != image_records:
        raise ValueError("evidence/manifest artifact records differ")
    total, decoded = 0, {}
    for record in records:
        path = _safe_artifact(root, record["path"])
        if path.stat().st_size != record["bytes"] or record["bytes"] > MAX_INPUT_BYTES:
            raise ValueError("artifact byte budget or size mismatch")
        if _hash(_read_bounded(path, MAX_INPUT_BYTES)) != record["sha256"]:
            raise ValueError("artifact hash mismatch")
        if path.name == "evidence.json":
            if "size" in record or "mode" in record:
                raise ValueError("JSON artifact cannot declare raster dimensions")
            continue
        if "size" not in record or "mode" not in record:
            raise ValueError("raster artifact requires dimensions and mode")
        with Image.open(path) as image:
            limit = MAX_BOARD_PIXELS if path.name == "evidence_board.png" else MAX_ROI_PIXELS
            if image.format != "PNG" or image.width * image.height > limit:
                raise ValueError("invalid raster or pixel budget")
            if list(image.size) != record["size"] or image.mode != record["mode"]:
                raise ValueError("artifact dimensions or mode mismatch")
            expected_mode = "L" if path.stem.endswith(("_edges", "_gradient")) else "RGB"
            if image.mode != expected_mode:
                raise ValueError("unexpected raster mode")
            if path.name not in {"palette.png", "evidence_board.png"} and list(image.size) != roi[2:]:
                raise ValueError("artifact dimensions do not match ROI")
            if path.name == "palette.png" and image.size != (256, 64):
                raise ValueError("invalid palette raster size")
            image.load()
            total += image.width * image.height
            decoded[path.name] = image.copy()
    if total > MAX_DERIVED_PIXELS or total != evidence["derived_pixels"]:
        raise ValueError("derived pixel budget or total mismatch")
    # Recompute the compact grid from actual raster sizes and label bounds.
    board, panels = _board({r["path"]: decoded[r["path"]] for r in image_records
                            if r["path"] != "evidence_board.png"})
    if decoded["evidence_board.png"].size != board.size:
        raise ValueError("invalid board dimensions for thumbnail and label layout")
    if evidence["panels"] != panels:
        raise ValueError("invalid board panel layout or mappings")
    x, y, _, _ = roi
    sl = [1, 0, 0, 1, -x, -y]
    lb = panels[0]["artifact_to_board"]
    if panels[0]["artifact"] != "source.png":
        raise ValueError("board source panel must come first")
    sb = multiply(lb, sl)
    expected_maps = {"source_to_local": sl, "local_to_source": inverse(sl),
                     "local_to_board": lb, "board_to_local": inverse(lb),
                     "source_to_board": sb, "board_to_source": inverse(sb)}
    for key, matrix in expected_maps.items():
        _check_matrix(evidence["mappings"][key], matrix)
    if "palette" in channels:
        palette, swatches = _palette(decoded["source.png"])
        if evidence["palette"] != palette or swatches.tobytes() != decoded["palette.png"].tobytes():
            raise ValueError("palette does not match source")
    elif evidence["palette"]:
        raise ValueError("unrequested palette metadata")
    return _receipt(root, channels)

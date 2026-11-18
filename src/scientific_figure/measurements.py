"""Bounded CPU pixel measurements, never semantics, OCR, Scene or true counts.

Public API: ``measure(request, output, *, base_directory=None)`` accepts a JSON
file or JSON dict and atomically publishes a *new* directory (result.json and
inspection_board.png). It returns the self-contained result, not a path receipt.
``validate_measurement(result)`` validates/copies that inline snapshot without
reading any files. Hashes are consistency checks, not independent authenticity.

Strict request keys: schema_version='0.1', source={path, sha256}, roi, question,
kind, background (required, null or '#RRGGBB'/RGB list), params, optional budget.
File-relative sources resolve against the request's parent unless base_directory
is supplied; dict-relative sources use base_directory or cwd. ROI and template_roi
are integer source [x,y,width,height]; bboxes are [x0,y0,x1,y1]. All coordinates
use EXIF-oriented pixel boundaries; sample points are integer pixel indices.
No current drawing geometry is stored. request_sha256 hashes the canonical JSON
request (normalized background/default params/full budget); result_sha256 hashes
canonical result JSON excluding result_sha256. cache_key ignores source.path and
question and binds source bytes, ROI, background, params, budgets and versions.
There is no persistent cache reader: consumers may reuse content by that key.

Minimal params (unknown fields are errors):
* view: optional context_margin=16 (0..1024), max_side=128 (16..512).
* profile: points=[[source_x,source_y],...], lines=[{start:[x,y], end:[x,y],
  samples:2..4096},...] (both lists required, at least one sample). Lines use
  endpoint-inclusive linear interpolation and floor(v+0.5) nearest sampling.
  Output includes sampled source/local indices, raw RGB, raw alpha, and RGB
  composited on the declared background (raw RGB when background=null).
* topology: thresholds=[0..255,...] (1..9 distinct integers), foreground='dark'
  or 'light', optional connectivity=4 or 8 (default 8). OpenCV RGB2GRAY uint8;
  dark is gray<=threshold, light is gray>threshold. Background components use
  dual connectivity. Only background components not touching the ROI boundary
  are hole *candidates*. Sensitivity compares component/hole pixel labels as well
  as counts; changed threshold masks are ambiguous, even if counts are equal.
* repeat: template_roi (same original source, may be outside search ROI),
  threshold (-1..1), optional scale=1 (only supported value), nms_iou=0.3
  (0..1), ambiguity_margin=0.02 (0..1). Grayscale TM_CCOEFF_NORMED, descending
  score then row/column order, greedy IoU NMS. Constant grayscale templates are
  unsupported. Multiple pre-NMS placements, near-threshold matches, and multiple
  candidates explicitly yield ambiguous. Scores are not semantic probabilities.

budget can only LOWER LIMITS below. candidates bounds each topology list and
all raw repeat matches before NMS; excess fails (never silently truncates).
match_work bounds placements*template_pixels before OpenCV; nms_work bounds raw
matches squared; samples bounds aggregate point/line samples; derived_pixels
includes decoded local RGBA buffers, topology/matching buffers and preview views.
input_pixels applies before decode; roi_pixels also bounds context/template.
result_bytes bounds the complete ASCII canonical JSON including the inline PNG.
Transparent topology/repeat without a background are unsupported; view/profile
can retain raw alpha. Null-background previews composite black solely for display.
"""
from __future__ import annotations

import base64
from io import BytesIO
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
import PIL

from .evidence import (MAX_BOARD_PIXELS, MAX_DERIVED_PIXELS, MAX_INPUT_BYTES,
                       MAX_INPUT_PIXELS, MAX_ROI_PIXELS, _background, _board,
                       _hash, _load_source, _read_bounded, _region,
                       _staged_directory)
from .geometry import inverse, multiply
from .prepared import (_fields, _json, _json_object, _number, _sha, _strings,
                       MAX_JSON_BYTES, COORDINATE_SPACE)

IMPLEMENTATION = "cpu-local-measurement-v1"
LIMITS = {"input_bytes": MAX_INPUT_BYTES, "input_pixels": MAX_INPUT_PIXELS,
          "roi_pixels": MAX_ROI_PIXELS, "board_pixels": MAX_BOARD_PIXELS,
          "derived_pixels": MAX_DERIVED_PIXELS, "samples": 4096,
          "candidates": 256, "match_work": 20_000_000, "nms_work": 65_536,
          "result_bytes": MAX_JSON_BYTES}
VERSIONS = {"numpy": np.__version__, "opencv": cv2.__version__, "pillow": PIL.__version__}
COMMON_LIMITATIONS = [
    "Pixel measurements and candidates only; no semantic identification, OCR or true object count.",
    "Threshold holes cannot establish transparency or scientific topology; inspect the source.",
    "The original source is external_not_packaged; the inline board and measurements are retained.",
    "Null-background board previews composite black for display only; raw alpha is separate.",
]


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _digest(value):
    return _hash(_canonical(value))


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"invalid {label}")
    return value


def _point(value, roi):
    if type(value) is not list or len(value) != 2:
        raise ValueError("point must be [source_x, source_y]")
    x, y, w, h = roi
    _integer(value[0], x, x + w - 1, "point x outside ROI")
    _integer(value[1], y, y + h - 1, "point y outside ROI")
    return value


def _budget(value):
    _fields(value, (), LIMITS)
    return {k: _integer(value.get(k, v), 1, v, f"{k} budget") for k, v in LIMITS.items()}


def _params(kind, params, roi, budget):
    if kind == "view":
        _fields(params, (), ("context_margin", "max_side"))
        return {"context_margin": _integer(params.get("context_margin", 16), 0, 1024, "context_margin"),
                "max_side": _integer(params.get("max_side", 128), 16, 512, "max_side")}
    if kind == "profile":
        _fields(params, ("points", "lines"))
        points, lines = params["points"], params["lines"]
        if type(points) is not list or type(lines) is not list:
            raise ValueError("points and lines must be lists")
        count = len(points)
        if count + 2 * len(lines) > budget["samples"]:
            raise ValueError("samples exceed budget")
        for value in points:
            _point(value, roi)
        for line in lines:
            _fields(line, ("start", "end", "samples"))
            _point(line["start"], roi)
            _point(line["end"], roi)
            count += _integer(line["samples"], 2, 4096, "line samples")
        if not 1 <= count <= budget["samples"]:
            raise ValueError("samples exceed budget or are empty")
        return params
    if kind == "topology":
        _fields(params, ("thresholds", "foreground"), ("connectivity",))
        thresholds = params["thresholds"]
        if type(thresholds) is not list or not 1 <= len(thresholds) <= 9:
            raise ValueError("thresholds must contain 1..9 values")
        for value in thresholds:
            _integer(value, 0, 255, "threshold")
        if len(set(thresholds)) != len(thresholds) or params["foreground"] not in ("dark", "light"):
            raise ValueError("invalid thresholds or foreground")
        connectivity = params.get("connectivity", 8)
        if type(connectivity) is not int or connectivity not in (4, 8):
            raise ValueError("connectivity must be 4 or 8")
        return {**params, "connectivity": connectivity}
    _fields(params, ("template_roi", "threshold"), ("scale", "nms_iou", "ambiguity_margin"))
    # Validate the shape before source dimensions become available.
    _region(params["template_roi"], (MAX_INPUT_PIXELS, MAX_INPUT_PIXELS))
    _number(params["threshold"], -1, 1, "threshold")
    _number(params.get("scale", 1), 0.001, 1000, "scale")
    _number(params.get("nms_iou", 0.3), 0, 1, "NMS IoU")
    _number(params.get("ambiguity_margin", 0.02), 0, 1, "ambiguity margin")
    return {"template_roi": params["template_roi"], "threshold": params["threshold"],
            "scale": params.get("scale", 1), "nms_iou": params.get("nms_iou", 0.3),
            "ambiguity_margin": params.get("ambiguity_margin", 0.02)}


def _request(value):
    value = _json_object(value)
    _fields(value, ("schema_version", "source", "roi", "question", "kind", "background", "params"), ("budget",))
    if value["schema_version"] != "0.1" or value["kind"] not in ("view", "profile", "topology", "repeat"):
        raise ValueError("unsupported measurement schema or kind")
    _fields(value["source"], ("path", "sha256"))
    _sha(value["source"]["sha256"])
    path = value["source"]["path"]
    if type(path) is not str or not path or len(path) > 4096 or "\0" in path:
        raise ValueError("invalid source path")
    if type(value["question"]) is not str or not value["question"].strip() or len(value["question"]) > 2000:
        raise ValueError("question must be a nonempty bounded string")
    if value["roi"] is None:
        raise ValueError("explicit ROI required")
    roi = _region(value["roi"], (MAX_INPUT_PIXELS, MAX_INPUT_PIXELS))
    budget = _budget(value.get("budget", {}))
    if roi[2] * roi[3] > budget["roi_pixels"]:
        raise ValueError("ROI exceeds pixel budget")
    return {**value, "roi": roi, "background": _background(value["background"]),
            "params": _params(value["kind"], value["params"], roi, budget), "budget": budget}


def _cache_key(request, versions=None):
    return _digest({"implementation": IMPLEMENTATION, "versions": VERSIONS if versions is None else versions,
                    "source_sha256": request["source"]["sha256"],
                    **{k: request[k] for k in ("roi", "background", "kind", "params", "budget")}})


def _charge(used, count, budget):
    used += count
    if used > budget["derived_pixels"]:
        raise ValueError("derived pixels exceed budget")
    return used


def _buffer_pixels(request, context_roi):
    area = request["roi"][2] * request["roi"][3]
    used = 3 * area + 3 * context_roi[2] * context_roi[3]
    if request["kind"] == "topology":
        used += area * (12 + len(request["params"]["thresholds"]))
    if request["kind"] == "repeat":
        template = request["params"]["template_roi"]
        used += 5 * template[2] * template[3] + 5 * area
    return used


def _crop(data, roi, background):
    with Image.open(BytesIO(data)) as raw:
        oriented = ImageOps.exif_transpose(raw)
        x, y, w, h = roi
        rgba = oriented.crop((x, y, x + w, y + h)).convert("RGBA")
    rgb = rgba.convert("RGB") if background is None else Image.alpha_composite(
        Image.new("RGBA", rgba.size, background), rgba).convert("RGB")
    return rgba, rgb


def _context(roi, size, margin):
    x, y, w, h = roi
    x0, y0 = max(0, x - margin), max(0, y - margin)
    return [x0, y0, min(size[0], x + w + margin) - x0,
            min(size[1], y + h + margin) - y0]


def _mappings(roi):
    x, y, _, _ = roi
    return {"source_to_local": [1, 0, 0, 1, -x, -y],
            "local_to_source": [1, 0, 0, 1, x, y]}


def _make_board(rgb, context_rgb, roi, context_roi, max_side):
    images = {}
    for name, image in (("roi.png", rgb), ("context.png", context_rgb)):
        image = image.copy()
        image.thumbnail((max_side, max_side), Image.Resampling.NEAREST)
        images[name] = image
    board, panels = _board(images)
    for panel, region in zip(panels, (roi, context_roi)):
        panel["source_roi"] = region
        # artifact_to_board maps the thumbnail, not the original full ROI.
        width, height = panel["rect"][2:]
        matrix = [width / region[2], 0, 0, height / region[3], panel["rect"][0], panel["rect"][1]]
        panel["source_to_board"] = multiply(matrix, _mappings(region)["source_to_local"])
        panel["board_to_source"] = inverse(panel["source_to_board"])
    buffer = BytesIO()
    board.save(buffer, format="PNG", compress_level=9)
    data = buffer.getvalue()
    return {"media_type": "image/png", "encoding": "base64", "content": base64.b64encode(data).decode("ascii"),
            "sha256": _hash(data), "size": list(board.size), "panels": panels}, data, sum(
                i.width * i.height for i in images.values()) + board.width * board.height


def _profile(rgba, rgb, roi, params):
    raw, display = np.asarray(rgba), np.asarray(rgb)
    def sample(point):
        x, y = point
        local = [x - roi[0], y - roi[1]]
        return {"source": point, "local": local, "raw_rgb": raw[local[1], local[0], :3].tolist(),
                "alpha": int(raw[local[1], local[0], 3]), "rgb": display[local[1], local[0]].tolist()}
    lines = []
    for line in params["lines"]:
        locations = np.floor(np.linspace(line["start"], line["end"], line["samples"]) + 0.5).astype(int)
        lines.append({"samples": [sample(p.tolist()) for p in locations]})
    return {"points": [sample(p) for p in params["points"]], "lines": lines}


def _components(mask, connectivity, roi, limit, holes=False):
    total, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=connectivity)
    edge = set(np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1])).tolist()) if holes else set()
    ids = [i for i in range(1, total) if i not in edge]
    if len(ids) > limit:
        raise ValueError("topology candidates exceed budget")
    result = []
    for i in ids:
        x, y, w, h, area = (int(v) for v in stats[i])
        result.append({"local_bbox": [x, y, x + w, y + h],
                       "source_bbox": [x + roi[0], y + roi[1], x + w + roi[0], y + h + roi[1]],
                       "pixels": area, "touches_roi_boundary": x == 0 or y == 0 or x + w == roi[2] or y + h == roi[3]})
    return result


def _topology(rgb, roi, params, budget):
    gray = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2GRAY)
    runs, masks = [], []
    for threshold in params["thresholds"]:
        mask = gray <= threshold if params["foreground"] == "dark" else gray > threshold
        components = _components(mask, params["connectivity"], roi, budget["candidates"])
        holes = _components(~mask, 12 - params["connectivity"], roi, budget["candidates"], holes=True)
        runs.append({"threshold": threshold, "foreground_pixels": int(mask.sum()),
                     "components": components, "hole_candidates": holes, "mask_sha256": _hash(mask.tobytes())})
        masks.append(mask)
    changes = [int(np.count_nonzero(masks[i] != masks[i - 1])) for i in range(1, len(masks))]
    sensitive = any(changes)
    return {"runs": runs, "sensitivity": {"changed_pixels_between_thresholds": changes,
            "sensitive": sensitive, "tested_thresholds": params["thresholds"]}}, "ambiguous" if sensitive else "ok"


def _iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return intersection / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection)


def _repeat(rgb, template, roi, params, budget):
    if params["scale"] != 1:
        return {"reason": "Only explicit single scale=1 is supported."}, "unsupported"
    gray = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2GRAY)
    target = cv2.cvtColor(np.asarray(template), cv2.COLOR_RGB2GRAY)
    if target.min() == target.max():
        return {"reason": "Constant grayscale template has undefined normalized correlation."}, "unsupported"
    h, w = target.shape
    if w > gray.shape[1] or h > gray.shape[0]:
        return {"reason": "Template is larger than the search ROI."}, "unsupported"
    placements = (gray.shape[0] - h + 1) * (gray.shape[1] - w + 1)
    if placements * w * h > budget["match_work"]:
        raise ValueError("template matching work exceeds budget")
    scores = cv2.matchTemplate(gray, target, cv2.TM_CCOEFF_NORMED)
    if not np.isfinite(scores).all():
        return {"reason": "Nonfinite normalized template scores."}, "unsupported"
    eligible = scores >= params["threshold"]
    count = int(np.count_nonzero(eligible))
    if count > budget["candidates"]:
        raise ValueError("raw repeat candidates exceed budget before NMS")
    if count * count > budget["nms_work"]:
        raise ValueError("NMS work exceeds budget")
    ys, xs = np.nonzero(eligible)
    order = sorted(zip(ys.tolist(), xs.tolist()), key=lambda p: (-float(scores[p]), p[0], p[1]))
    kept, suppressed = [], 0
    for y, x in order:
        bbox = [x, y, x + w, y + h]
        if any(_iou(bbox, old["local_bbox"]) > params["nms_iou"] for old in kept):
            suppressed += 1
            continue
        kept.append({"local_bbox": bbox, "source_bbox": [x + roi[0], y + roi[1], x + w + roi[0], y + h + roi[1]],
                     "score": float(scores[y, x])})
    reasons = []
    if count > len(kept):
        reasons.append("Multiple overlapping placements exceeded threshold before NMS.")
    if len(kept) > 1:
        reasons.append("Multiple similar placements; identity and true count are unresolved.")
    if any(c["score"] - params["threshold"] <= params["ambiguity_margin"] for c in kept):
        reasons.append("Candidate scores are close to the selected threshold.")
    return {"candidates": kept, "placements_evaluated": placements, "raw_candidates": count,
            "suppressed": suppressed, "ambiguity_reasons": reasons}, "ambiguous" if reasons else "ok"


def measure(request, output, *, base_directory=None):
    """Measure an explicit ROI and return/publish one portable validated snapshot."""
    if type(request) is dict:
        value, default_base = request, Path.cwd()
    else:
        path = Path(request).resolve()
        value, default_base = _json(_read_bounded(path, MAX_JSON_BYTES)), path.parent
    request = _request(value)
    budget, roi, params = request["budget"], request["roi"], request["params"]
    path = Path(request["source"]["path"])
    if not path.is_absolute():
        path = Path(base_directory) / path if base_directory is not None else default_base / path
    data = _read_bounded(path, budget["input_bytes"])
    if _hash(data) != request["source"]["sha256"]:
        raise ValueError("source SHA256 mismatch")
    with Image.open(BytesIO(data)) as raw:
        if raw.width * raw.height > budget["input_pixels"]:
            raise ValueError("input pixels exceed budget")
    # Reuse evidence's decode/orientation/frame/ROI checks. Black is a display-only
    # fallback for null; raw RGBA and measurement assumptions are handled below.
    _, source = _load_source(path, roi, request["background"] or "#000000")
    if source["sha256"] != request["source"]["sha256"]:
        raise ValueError("source changed during measurement")
    roi = _region(roi, source["size"])
    margin = params["context_margin"] if request["kind"] == "view" else 0
    context_roi = _region(_context(roi, source["size"], margin), source["size"])
    if context_roi[2] * context_roi[3] > budget["roi_pixels"]:
        raise ValueError("context ROI exceeds pixel budget")
    template_roi = None
    if request["kind"] == "repeat":
        template_roi = _region(params["template_roi"], source["size"])
        if template_roi[2] * template_roi[3] > budget["roi_pixels"]:
            raise ValueError("template ROI exceeds pixel budget")
    # Conservative aggregate buffer accounting, before allocations/algorithms.
    used = _charge(0, _buffer_pixels(request, context_roi), budget)
    rgba, rgb = _crop(data, roi, request["background"])
    context_rgba, context_rgb = _crop(data, context_roi, request["background"])
    status, limitations = "ok", list(COMMON_LIMITATIONS)
    method = {"implementation": IMPLEMENTATION, "kind": request["kind"], "params": params,
              "background": request["background"], "versions": VERSIONS,
              "grayscale": "OpenCV COLOR_RGB2GRAY uint8", "sample_rule": "nearest-floor(v+0.5)",
              "repeat_algorithm": "TM_CCOEFF_NORMED; greedy IoU NMS; score,row,column order"}
    transparent = rgba.getchannel("A").getextrema()[0] < 255
    template = None
    if template_roi is not None:
        template_rgba, template = _crop(data, template_roi, request["background"])
        transparent |= template_rgba.getchannel("A").getextrema()[0] < 255
    if request["kind"] in ("topology", "repeat") and transparent and request["background"] is None:
        measurements, status = {"reason": "Transparent pixels require an explicit measurement background."}, "unsupported"
    elif request["kind"] == "view":
        measurements = {"roi": roi, "context_roi": context_roi}
    elif request["kind"] == "profile":
        measurements = _profile(rgba, rgb, roi, params)
    elif request["kind"] == "topology":
        measurements, status = _topology(rgb, roi, params, budget)
        if len(params["thresholds"]) == 1:
            limitations.append("Only one threshold tested; threshold sensitivity is unassessed.")
    else:
        measurements, status = _repeat(rgb, template, roi, params, budget)
        limitations.append("Single-scale grayscale correlation; color, rotation and scale changes are not resolved.")
    # Preview compositing is explicit and never modifies measurement samples.
    if request["background"] is None:
        _, rgb = _crop(data, roi, "#000000")
        _, context_rgb = _crop(data, context_roi, "#000000")
    board, board_bytes, board_pixels = _make_board(rgb, context_rgb, roi, context_roi, params.get("max_side", 128))
    if math.prod(board["size"]) > budget["board_pixels"]:
        raise ValueError("board exceeds pixel budget")
    used = _charge(used, board_pixels, budget)
    result = {"schema_version": "0.1", "kind": "measurement_result", "request": request,
              "request_sha256": _digest(request), "source": {"sha256": source["sha256"],
              "bytes": source["bytes"], "size": source["size"], "coordinate_space": COORDINATE_SPACE,
              "packaging": "external_not_packaged"}, "roi": roi, "mappings": _mappings(roi),
              "method": method, "measurements": measurements, "limitations": limitations,
              "status": status, "cache_key": _cache_key(request), "board": board,
              "derived_pixels": used}
    result["result_sha256"] = _digest(result)
    if len(_canonical(result)) > budget["result_bytes"]:
        raise ValueError("result JSON exceeds byte budget")
    result = validate_measurement(result)
    with _staged_directory(output) as (stage, _):
        (stage / "result.json").write_bytes(_canonical(result) + b"\n")
        (stage / "inspection_board.png").write_bytes(board_bytes)
    return result


def _bbox_pair(value, roi):
    for key in ("local_bbox", "source_bbox"):
        box = value[key]
        if type(box) is not list or len(box) != 4 or any(type(v) is not int for v in box):
            raise ValueError("invalid candidate bbox")
    x0, y0, x1, y1 = value["local_bbox"]
    if not (0 <= x0 < x1 <= roi[2] and 0 <= y0 < y1 <= roi[3]):
        raise ValueError("candidate bbox outside ROI")
    if value["source_bbox"] != [x0 + roi[0], y0 + roi[1], x1 + roi[0], y1 + roi[1]]:
        raise ValueError("candidate source/local bbox mismatch")


def _validate_values(result):
    request, values, roi = result["request"], result["measurements"], result["roi"]
    params, kind, budget = request["params"], request["kind"], request["budget"]
    if result["status"] == "unsupported":
        _fields(values, ("reason",))
        reasons = {"topology": {"Transparent pixels require an explicit measurement background."},
                   "repeat": {"Transparent pixels require an explicit measurement background.",
                              "Only explicit single scale=1 is supported.",
                              "Constant grayscale template has undefined normalized correlation.",
                              "Template is larger than the search ROI.",
                              "Nonfinite normalized template scores."}}
        if type(values["reason"]) is not str or values["reason"] not in reasons.get(kind, set()):
            raise ValueError("unsupported reason does not match measurement kind")
        if values["reason"] == "Transparent pixels require an explicit measurement background." and request["background"] is not None:
            raise ValueError("unsupported alpha assumption mismatch")
        if values["reason"] == "Only explicit single scale=1 is supported." and params["scale"] == 1:
            raise ValueError("unsupported scale mismatch")
        return
    if kind in ("view", "profile") and result["status"] != "ok":
        raise ValueError("view/profile status must be ok")
    if kind == "view":
        _fields(values, ("roi", "context_roi"))
        if values["roi"] != roi or values["context_roi"] != _context(roi, result["source"]["size"], params["context_margin"]):
            raise ValueError("view ROI mismatch")
    elif kind == "profile":
        _fields(values, ("points", "lines"))
        if type(values["points"]) is not list or len(values["points"]) != len(params["points"]) or type(values["lines"]) is not list or len(values["lines"]) != len(params["lines"]):
            raise ValueError("profile length mismatch")
        def sample(item, expected):
            _fields(item, ("source", "local", "raw_rgb", "alpha", "rgb"))
            _point(item["source"], roi)
            if item["source"] != expected or item["local"] != [expected[0] - roi[0], expected[1] - roi[1]]:
                raise ValueError("profile source/local mismatch")
            for key in ("raw_rgb", "rgb"):
                if type(item[key]) is not list or len(item[key]) != 3:
                    raise ValueError("invalid RGB")
                for channel in item[key]:
                    _integer(channel, 0, 255, "RGB")
            _integer(item["alpha"], 0, 255, "alpha")
            if request["background"] is None and item["rgb"] != item["raw_rgb"]:
                raise ValueError("null background requires raw RGB")
        for item, expected in zip(values["points"], params["points"]):
            sample(item, expected)
        for line, declared in zip(values["lines"], params["lines"]):
            _fields(line, ("samples",))
            if type(line["samples"]) is not list or len(line["samples"]) != declared["samples"]:
                raise ValueError("line sample length mismatch")
            positions = np.floor(np.linspace(declared["start"], declared["end"], declared["samples"]) + 0.5).astype(int)
            for item, expected in zip(line["samples"], positions.tolist()):
                sample(item, expected)
    elif kind == "topology":
        _fields(values, ("runs", "sensitivity"))
        if type(values["runs"]) is not list or len(values["runs"]) != len(params["thresholds"]):
            raise ValueError("threshold runs mismatch")
        hashes = []
        for run, threshold in zip(values["runs"], params["thresholds"]):
            _fields(run, ("threshold", "foreground_pixels", "components", "hole_candidates", "mask_sha256"))
            if run["threshold"] != threshold:
                raise ValueError("threshold mismatch")
            _sha(run["mask_sha256"])
            hashes.append(run["mask_sha256"])
            _integer(run["foreground_pixels"], 0, roi[2] * roi[3], "foreground pixels")
            for key in ("components", "hole_candidates"):
                entries = run[key]
                if type(entries) is not list or len(entries) > budget["candidates"]:
                    raise ValueError("candidate budget exceeded")
                for item in entries:
                    _fields(item, ("local_bbox", "source_bbox", "pixels", "touches_roi_boundary"))
                    _bbox_pair(item, roi)
                    b = item["local_bbox"]
                    _integer(item["pixels"], 1, (b[2] - b[0]) * (b[3] - b[1]), "component pixels")
                    boundary = b[0] == 0 or b[1] == 0 or b[2] == roi[2] or b[3] == roi[3]
                    if type(item["touches_roi_boundary"]) is not bool or item["touches_roi_boundary"] != boundary or (key == "hole_candidates" and boundary):
                        raise ValueError("invalid boundary candidate")
            if sum(i["pixels"] for i in run["components"]) != run["foreground_pixels"]:
                raise ValueError("component pixel sum mismatch")
        sensitivity = values["sensitivity"]
        _fields(sensitivity, ("changed_pixels_between_thresholds", "sensitive", "tested_thresholds"))
        changes = sensitivity["changed_pixels_between_thresholds"]
        if type(changes) is not list or len(changes) != len(hashes) - 1 or sensitivity["tested_thresholds"] != params["thresholds"]:
            raise ValueError("invalid threshold sensitivity")
        for index, change in enumerate(changes):
            _integer(change, 0, roi[2] * roi[3], "changed pixels")
            if bool(change) != (hashes[index] != hashes[index + 1]):
                raise ValueError("mask hash/sensitivity mismatch")
        if type(sensitivity["sensitive"]) is not bool or sensitivity["sensitive"] != any(changes) or result["status"] != ("ambiguous" if any(changes) else "ok"):
            raise ValueError("sensitivity status mismatch")
    else:
        _fields(values, ("candidates", "placements_evaluated", "raw_candidates", "suppressed", "ambiguity_reasons"))
        candidates = values["candidates"]
        if type(candidates) is not list or len(candidates) > budget["candidates"]:
            raise ValueError("candidate budget exceeded")
        _integer(values["raw_candidates"], len(candidates), budget["candidates"], "raw candidates")
        _integer(values["suppressed"], 0, values["raw_candidates"], "suppressed")
        if len(candidates) + values["suppressed"] != values["raw_candidates"] or values["raw_candidates"] ** 2 > budget["nms_work"]:
            raise ValueError("NMS accounting mismatch")
        tw, th = params["template_roi"][2:]
        placements = (roi[2] - tw + 1) * (roi[3] - th + 1)
        if tw > roi[2] or th > roi[3] or placements != values["placements_evaluated"] or placements * tw * th > budget["match_work"]:
            raise ValueError("matching work mismatch")
        for i, item in enumerate(candidates):
            _fields(item, ("local_bbox", "source_bbox", "score"))
            _bbox_pair(item, roi)
            _number(item["score"], params["threshold"], 1, "candidate score")
            box = item["local_bbox"]
            if box[2] - box[0] != tw or box[3] - box[1] != th or any(_iou(box, c["local_bbox"]) > params["nms_iou"] for c in candidates[:i]):
                raise ValueError("invalid template size or NMS candidates")
        expected_order = sorted(candidates, key=lambda c: (-c["score"], c["local_bbox"][1], c["local_bbox"][0]))
        if candidates != expected_order:
            raise ValueError("repeat order mismatch")
        _strings(values["ambiguity_reasons"])
        ambiguous = values["suppressed"] > 0 or len(candidates) > 1 or any(c["score"] - params["threshold"] <= params["ambiguity_margin"] for c in candidates)
        if bool(values["ambiguity_reasons"]) != ambiguous or result["status"] != ("ambiguous" if ambiguous else "ok"):
            raise ValueError("repeat ambiguity mismatch")


def validate_measurement(result):
    """Return a detached, validated inline snapshot; never open source/output paths.

    Validates bounded JSON, canonical hashes, request/method/source bindings,
    coordinate mappings, inline PNG integrity/dimensions and kind-specific records.
    It cannot authenticate pixels or conclusions against a missing original.
    """
    result = _json_object(result)
    _fields(result, ("schema_version", "kind", "request", "request_sha256", "source", "roi", "mappings",
                     "method", "measurements", "limitations", "status", "cache_key", "board", "derived_pixels", "result_sha256"))
    if result["schema_version"] != "0.1" or result["kind"] != "measurement_result" or result["status"] not in ("ok", "ambiguous", "unsupported"):
        raise ValueError("invalid result schema or status")
    for key in ("request_sha256", "result_sha256", "cache_key"):
        _sha(result[key])
    if _digest({k: v for k, v in result.items() if k != "result_sha256"}) != result["result_sha256"]:
        raise ValueError("result SHA256 mismatch")
    request = _request(result["request"])
    if request != result["request"] or _digest(request) != result["request_sha256"]:
        raise ValueError("request SHA256 or normalization mismatch")
    budget, roi = request["budget"], request["roi"]
    if len(_canonical(result)) > budget["result_bytes"]:
        raise ValueError("result JSON exceeds byte budget")
    source = result["source"]
    _fields(source, ("sha256", "size", "bytes", "coordinate_space", "packaging"))
    _sha(source["sha256"])
    if source["sha256"] != request["source"]["sha256"] or source["coordinate_space"] != COORDINATE_SPACE or source["packaging"] != "external_not_packaged":
        raise ValueError("source binding mismatch")
    if type(source["size"]) is not list or len(source["size"]) != 2:
        raise ValueError("invalid source size")
    for v in source["size"]:
        _integer(v, 1, budget["input_pixels"], "source dimension")
    if math.prod(source["size"]) > budget["input_pixels"]:
        raise ValueError("source pixels exceed budget")
    _integer(source["bytes"], 1, budget["input_bytes"], "source bytes")
    if result["roi"] != _region(roi, source["size"]) or result["mappings"] != _mappings(roi):
        raise ValueError("ROI or coordinate mappings mismatch")
    if request["kind"] == "repeat":
        template = _region(request["params"]["template_roi"], source["size"])
        if template[2] * template[3] > budget["roi_pixels"]:
            raise ValueError("template budget exceeded")
    method = result["method"]
    _fields(method, ("implementation", "kind", "params", "background", "versions", "grayscale", "sample_rule", "repeat_algorithm"))
    versions = method["versions"]
    _fields(versions, ("numpy", "opencv", "pillow"))
    if any(type(v) is not str or not 1 <= len(v) <= 100 for v in versions.values()):
        raise ValueError("invalid recorded library versions")
    if result["cache_key"] != _cache_key(request, versions):
        raise ValueError("cache key mismatch")
    if method != {"implementation": IMPLEMENTATION, "kind": request["kind"], "params": request["params"],
                  "background": request["background"], "versions": versions, "grayscale": "OpenCV COLOR_RGB2GRAY uint8",
                  "sample_rule": "nearest-floor(v+0.5)", "repeat_algorithm": "TM_CCOEFF_NORMED; greedy IoU NMS; score,row,column order"}:
        raise ValueError("method binding mismatch")
    _strings(result["limitations"], maximum=20)
    if not all(item in result["limitations"] for item in COMMON_LIMITATIONS):
        raise ValueError("missing measurement limitations")
    board = result["board"]
    _fields(board, ("media_type", "encoding", "content", "sha256", "size", "panels"))
    if board["media_type"] != "image/png" or board["encoding"] != "base64" or type(board["content"]) is not str:
        raise ValueError("invalid inline board")
    _sha(board["sha256"])
    try:
        data = base64.b64decode(board["content"], validate=True)
    except ValueError as exc:
        raise ValueError("invalid inline board base64") from exc
    if _hash(data) != board["sha256"]:
        raise ValueError("inline board SHA256 mismatch")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG" or image.mode != "RGB" or list(image.size) != board["size"] or image.width * image.height > budget["board_pixels"] or getattr(image, "n_frames", 1) != 1:
                raise ValueError("inline board dimensions or format mismatch")
            image.verify()
    except (OSError, SyntaxError) as exc:
        raise ValueError("invalid inline board PNG") from exc
    panels = board["panels"]
    margin = request["params"]["context_margin"] if request["kind"] == "view" else 0
    context = _context(roi, source["size"], margin)
    _region(context, source["size"])
    if context[2] * context[3] > budget["roi_pixels"] or type(panels) is not list or len(panels) != 2:
        raise ValueError("invalid context or panels")
    expected_views = {}
    max_side = request["params"].get("max_side", 128)
    for name, region in zip(("roi.png", "context.png"), (roi, context)):
        image = Image.new("RGB", tuple(region[2:]))
        image.thumbnail((max_side, max_side), Image.Resampling.NEAREST)
        expected_views[name] = image
    previous_right = 0
    for panel, (name, thumbnail), region in zip(panels, expected_views.items(), (roi, context)):
        _fields(panel, ("artifact", "rect", "artifact_to_board", "board_to_artifact", "source_roi", "source_to_board", "board_to_source"))
        rect = panel["rect"]
        if type(rect) is not list or len(rect) != 4 or any(type(v) is not int for v in rect):
            raise ValueError("invalid board panel rect")
        x, y, width, height = rect
        scale = min(1, 384 / thumbnail.width, 384 / thumbnail.height)
        expected_size = [max(1, int(thumbnail.width * scale)), max(1, int(thumbnail.height * scale))]
        if panel["artifact"] != name or panel["source_roi"] != region or [width, height] != expected_size or x < previous_right or y < 24 or x + width > board["size"][0] or y + height > board["size"][1]:
            raise ValueError("panel layout mismatch")
        previous_right = x + width
        artifact_map = [width / thumbnail.width, 0, 0, height / thumbnail.height, x, y]
        if panel["artifact_to_board"] != artifact_map or panel["board_to_artifact"] != inverse(artifact_map):
            raise ValueError("panel thumbnail mapping mismatch")
        mapping = multiply([width / region[2], 0, 0, height / region[3], x, y], _mappings(region)["source_to_local"])
        if panel["source_to_board"] != mapping or panel["board_to_source"] != inverse(mapping):
            raise ValueError("panel source mapping mismatch")
    _integer(result["derived_pixels"], 1, budget["derived_pixels"], "derived pixels")
    expected_pixels = _buffer_pixels(request, context) + math.prod(board["size"]) + sum(
        image.width * image.height for image in expected_views.values())
    if result["derived_pixels"] != expected_pixels:
        raise ValueError("derived pixel accounting mismatch")
    _validate_values(result)
    return result

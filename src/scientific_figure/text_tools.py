"""Measured text proposals and a richer, separate text inventory data contract.

These functions return JSON data. They never rewrite a Scene or accept text
suggestions on the host's behalf. Rendering retains its existing 1.2x line spacing.
"""
from __future__ import annotations

import hashlib
import math

from PIL import ImageFont

from .evidence import MAX_INPUT_PIXELS, _check_matrix, _read_bounded, _region
from .fonts import check_text, font_info, text_bounds
from .geometry import inverse
from .prepared import (COORDINATE_SPACE, _bbox, _fields, _identifier,
                       _json_object, _number, _sha, _strings)

MAX_TEXT = 10000
MAX_LINES = 256
MAX_ENTRIES = 3000
MEASUREMENT_SCALE = 8
LINE_SPACING = 1.2


def _text(value, *, nullable=False):
    if nullable and value is None:
        return
    if type(value) is not str or len(value) > MAX_TEXT:
        raise ValueError("text exceeds character budget or is not a string")
    if any((ord(c) < 32 and c != "\n") or 127 <= ord(c) < 160 for c in value):
        raise ValueError("unsupported control character in text")
    if len(value.split("\n")) > MAX_LINES:
        raise ValueError("text exceeds line budget")


def _font(text, font_size, font_family):
    _text(text)
    _number(font_size, 0.125, 512, "font size")
    if font_family is not None and (type(font_family) is not str or not 1 <= len(font_family) <= 200):
        raise ValueError("invalid font family")
    check_text(text, font_family)
    info = font_info()
    # font_info is cached: a changed file must not inherit the old font's identity.
    if hashlib.sha256(_read_bounded(info["path"], 32 * 1024 * 1024)).hexdigest() != info["sha256"]:
        raise ValueError("selected font changed; refresh font selection before measuring")
    font = ImageFont.truetype(info["path"], max(1, round(font_size * MEASUREMENT_SCALE)))
    return font, {key: info[key] for key in ("path", "family", "sha256")}


def _line_width(font, line):
    left, _, right, _ = font.getbbox(line, anchor="ls")
    advance = font.getlength(line)
    return (max(right, advance) - min(0, left)) / MEASUREMENT_SCALE


def _measure(text, font_size, font, identity, target_width, target_height):
    lines = text.split("\n")
    advances = [font.getlength(line) / MEASUREMENT_SCALE for line in lines]
    widths = [_line_width(font, line) for line in lines]
    # Same baseline, 8x measurement and fixed line spacing as fonts.text_bounds.
    bbox = list(text_bounds({"x": 0, "y": 0, "text": text},
                            {"font_size": font_size, "anchor": "start"}))
    height = max(font_size * LINE_SPACING * len(lines), bbox[3] - bbox[1])
    width = max(widths)
    fits = {"width": None if target_width is None else width <= target_width,
            "height": None if target_height is None else height <= target_height}
    return {"schema_version": "0.1", "kind": "text_measurement", "text": text,
            "font": identity, "font_size": font_size,
            "effective_font_size": round(font_size * MEASUREMENT_SCALE) / MEASUREMENT_SCALE,
            "measurement_scale": MEASUREMENT_SCALE, "line_spacing": LINE_SPACING,
            "line_advance": font_size * LINE_SPACING, "line_count": len(lines),
            "line_widths": widths, "line_advances": advances, "width": width,
            "height": height, "ink_bbox": bbox, "fits_target": fits,
            "limitations": ["Mechanical font bounds, not optical layout, overlap or occlusion review.",
                            "No fallback, rich text, letter spacing or adjustable line spacing."]}


def _target(value, label):
    if value is not None:
        _number(value, 0.001, 1_000_000, label)


def measure_text(text, *, font_size=14, font_family=None, target_width=None, target_height=None):
    """Measure at baseline (0, 0), using the actual selected font's 8x metrics.

    width includes advances and overhanging ink; height includes fixed 1.2x line
    boxes and ink extent. ink_bbox is the existing renderer's text_bounds result.
    Targets are mechanical checks only, independent of other Scene objects.
    """
    _target(target_width, "target width")
    _target(target_height, "target height")
    font, identity = _font(text, font_size, font_family)
    return _measure(text, font_size, font, identity, target_width, target_height)


def wrap_text(text, max_width, *, font_size=14, font_family=None, max_lines=MAX_LINES):
    """Propose explicit measured line breaks, preserving every original character.

    Existing newlines remain. Whitespace is retained, including trailing spaces.
    Break at the last fitting whitespace where possible, otherwise at a Unicode
    codepoint. A single overwide codepoint remains visible with fits=False.
    max_lines is a target, not a truncation request. Above 256 output lines the
    operation refuses instead of silently dropping content. No hyphenation,
    grapheme segmentation or optical typesetting is claimed.
    """
    _target(max_width, "wrap width")
    if max_width is None:
        raise ValueError("wrap width is required")
    if type(max_lines) is not int or not 1 <= max_lines <= MAX_LINES:
        raise ValueError("invalid target line count")
    font, identity = _font(text, font_size, font_family)
    output = []
    for paragraph in text.split("\n"):
        remaining = paragraph
        if not remaining:
            output.append("")
        while remaining:
            if len(output) >= MAX_LINES:
                raise ValueError("wrapped text exceeds line budget")
            if _line_width(font, remaining) <= max_width:
                output.append(remaining)
                break
            low, high = 0, len(remaining)
            while low < high:
                middle = (low + high + 1) // 2
                if _line_width(font, remaining[:middle]) <= max_width:
                    low = middle
                else:
                    high = middle - 1
            cut = max(1, low)
            whitespace = [i + 1 for i, c in enumerate(remaining[:cut]) if c.isspace()]
            if whitespace:
                cut = whitespace[-1]
            output.append(remaining[:cut])
            remaining = remaining[cut:]
        if len(output) > MAX_LINES:
            raise ValueError("wrapped text exceeds line budget")
    proposed = "\n".join(output)
    # Output can grow by at most 255 inserted newlines; no revalidation against
    # the input character limit is needed for this already bounded result.
    measured = _measure(proposed, font_size, font, identity, max_width, None)
    overflow = [index for index, width in enumerate(measured["line_widths"]) if width > max_width]
    return {"schema_version": "0.1", "kind": "text_wrap_proposal", "original": text,
            "text": proposed, "lines": output, "max_width": max_width,
            "max_lines": max_lines, "fits": not overflow and len(output) <= max_lines,
            "overflow_lines": overflow, "line_count_exceeded": len(output) > max_lines,
            "measurement": measured, "scene_mutation": False,
            "limitations": ["Explicit proposal only; caller chooses whether to apply it.",
                            "Breaks use codepoints and whitespace, not grapheme-aware typesetting."]}


def _inventory_source(source):
    if source is None:
        return
    _fields(source, {"image_sha256", "coordinate_space", "source_size", "roi", "bbox"}, {"mappings"})
    _sha(source["image_sha256"])
    size = source["source_size"]
    if type(size) is not list or len(size) != 2 or any(type(v) is not int or v <= 0 for v in size):
        raise ValueError("invalid inventory source size")
    if math.prod(size) > MAX_INPUT_PIXELS:
        raise ValueError("inventory source exceeds pixel budget")
    if source["coordinate_space"] != COORDINATE_SPACE:
        raise ValueError("inventory requires source coordinates")
    roi = _region(source["roi"], size)
    _bbox(source["bbox"], roi)
    if "mappings" in source:
        mappings = source["mappings"]
        _fields(mappings, {"source_to_local", "local_to_source"})
        expected = [1, 0, 0, 1, -roi[0], -roi[1]]
        for key, matrix in {"source_to_local": expected, "local_to_source": inverse(expected)}.items():
            value = mappings[key]
            if type(value) is not list or len(value) != 6:
                raise ValueError("invalid inventory coordinate mapping")
            for number in value:
                _number(number, -10_000_000, 10_000_000, "mapping component")
            _check_matrix(value, matrix)


def validate_inventory(document, *, object_ids=None):
    """Validate a separate rich text inventory, preserving original and proposals.

    Document: schema_version='0.1', kind='text_inventory', id, entries. Each entry
    requires entry_id, original, normalized (string/null), suggestion (string/null),
    uncertainty (strings), source (null or SHA/size/ROI/bbox/source coordinates).
    Optional: object_id, evidence_refs ([{id, sha256}]), resolution (unresolved,
    original, normalized, suggestion). Optional source mappings contain the inverse
    source_to_local/local_to_source pair. object_ids, if supplied, is the host's
    current Scene ID list; unknown object bindings are rejected. No Scene changes
    or claims of actual image verification result from this metadata validation.
    """
    doc = _json_object(document, limit=4 * 1024 * 1024)
    _fields(doc, {"schema_version", "kind", "id", "entries"})
    if doc["schema_version"] != "0.1" or doc["kind"] != "text_inventory":
        raise ValueError("unsupported inventory format")
    _identifier(doc["id"])
    if object_ids is not None:
        if type(object_ids) is not list or len(object_ids) > MAX_ENTRIES:
            raise ValueError("invalid object ID list")
        for oid in object_ids:
            _identifier(oid)
        if len(set(object_ids)) != len(object_ids):
            raise ValueError("duplicate object IDs")
    if type(doc["entries"]) is not list or len(doc["entries"]) > MAX_ENTRIES:
        raise ValueError("inventory exceeds entry budget")
    ids, bound_objects = set(), set()
    for entry in doc["entries"]:
        _fields(entry, {"entry_id", "original", "normalized", "suggestion", "uncertainty", "source"},
                {"object_id", "evidence_refs", "resolution"})
        _identifier(entry["entry_id"])
        if entry["entry_id"] in ids:
            raise ValueError("duplicate inventory entry ID")
        ids.add(entry["entry_id"])
        _text(entry["original"])
        _text(entry["normalized"], nullable=True)
        _text(entry["suggestion"], nullable=True)
        _strings(entry["uncertainty"])
        _inventory_source(entry["source"])
        if "object_id" in entry:
            oid = entry["object_id"]
            _identifier(oid)
            if oid in bound_objects or object_ids is not None and oid not in object_ids:
                raise ValueError("duplicate or unknown inventory object binding")
            bound_objects.add(oid)
        refs = entry.get("evidence_refs", [])
        if type(refs) is not list or len(refs) > 100:
            raise ValueError("invalid evidence references")
        ref_ids = set()
        for ref in refs:
            _fields(ref, {"id", "sha256"})
            _identifier(ref["id"])
            _sha(ref["sha256"])
            if ref["id"] in ref_ids:
                raise ValueError("duplicate evidence reference")
            ref_ids.add(ref["id"])
        resolution = entry.get("resolution", "unresolved")
        if resolution not in ("unresolved", "original", "normalized", "suggestion"):
            raise ValueError("invalid inventory resolution")
        if resolution in ("normalized", "suggestion") and entry[resolution] is None:
            raise ValueError("resolved proposal is absent")
    return {"schema_version": "0.1", "kind": "text_inventory_validation", "valid": True,
            "document": doc, "object_bindings_checked": object_ids is not None,
            "scene_mutation": False, "source_content_verified": False,
            "limitations": ["Inventory metadata only; source images and evidence references are not read.",
                            "Original text is preserved; acceptance does not write Scene text."]}

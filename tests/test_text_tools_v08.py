"""Real selected-font measurements, explicit wrap proposals and rich inventory."""
import copy
import hashlib
import json
from pathlib import Path

from PIL import ImageFont
import pytest

from scientific_figure.fonts import font_info, text_bounds
from scientific_figure.text_tools import measure_text, validate_inventory, wrap_text


def test_measurement_uses_real_selected_font_and_existing_renderer_bounds():
    text, size = "PTL alpha\nWW iii", 14.25
    info = font_info()
    font = ImageFont.truetype(info["path"], round(size * 8))
    result = measure_text(text, font_size=size, font_family=info["family"])
    assert result["font"]["sha256"] == hashlib.sha256(Path(info["path"]).read_bytes()).hexdigest()
    assert result["font"]["family"] == info["family"]
    assert result["line_advances"] == [font.getlength(line) / 8 for line in text.split("\n")]
    assert result["ink_bbox"] == list(text_bounds({"x": 0, "y": 0, "text": text},
                                                 {"font_size": size, "anchor": "start"}))
    assert result["line_advance"] == size * 1.2
    assert result["line_spacing"] == 1.2
    assert measure_text("WWWW")["width"] > measure_text("iiii")["width"]
    assert measure_text(text, font_size=size) == result
    json.dumps(result, allow_nan=False)


def test_target_checks_are_explicit_mechanical_results():
    result = measure_text("PTL", target_width=1, target_height=1)
    assert result["fits_target"] == {"width": False, "height": False}
    result = measure_text("PTL", target_width=1000, target_height=1000)
    assert result["fits_target"] == {"width": True, "height": True}
    assert measure_text("")["line_count"] == 1
    assert measure_text("PTL")["fits_target"] == {"width": None, "height": None}


def test_wrap_is_deterministic_measured_and_preserves_input_characters():
    original = "alpha beta gamma delta PTL"
    width = measure_text("alpha beta ")["width"]
    result = wrap_text(original, width)
    assert result == wrap_text(original, width)
    assert result["original"] == original
    assert "".join(result["lines"]) == original
    assert result["text"] == "\n".join(result["lines"])
    assert result["scene_mutation"] is False and result["fits"] is True
    assert len(result["lines"]) > 1
    assert result["lines"][0].endswith(" ")
    for line in result["lines"]:
        assert measure_text(line)["width"] <= width
    assert result["measurement"] == measure_text(result["text"], target_width=width)


def test_wrap_preserves_newlines_blank_lines_and_trailing_whitespace():
    original = "alpha beta\n\nPTL  "
    result = wrap_text(original, 200)
    assert result["text"] == original
    assert result["lines"] == ["alpha beta", "", "PTL  "]
    assert result["fits"]


def test_wrap_reports_overflow_without_truncation():
    result = wrap_text("WWW", 1, max_lines=2)
    assert result["lines"] == ["W", "W", "W"]
    assert result["fits"] is False
    assert result["overflow_lines"] == [0, 1, 2]
    assert result["line_count_exceeded"] is True
    assert "".join(result["lines"]) == result["original"]
    with pytest.raises(ValueError, match="line budget"):
        wrap_text("W" * 257, 1)


@pytest.mark.parametrize("kwargs", [
    {"font_size": True}, {"font_size": 0}, {"font_size": 513},
    {"font_size": float("inf")}, {"target_width": float("nan")},
    {"target_height": -1}, {"font_family": "not-the-selected-font"},
    {"font_family": ""}, {"font_size": 10 ** 1000},
])
def test_invalid_metric_parameters_fail(kwargs):
    with pytest.raises(ValueError):
        measure_text("PTL", **kwargs)


@pytest.mark.parametrize("text", ["x" * 10001, "\n" * 256, "a\tb", "a\x00b"])
def test_text_limits_and_unsupported_controls(text):
    with pytest.raises(ValueError):
        measure_text(text)
    with pytest.raises(ValueError):
        wrap_text(text, 100)


def test_selected_font_sha_cannot_silently_survive_changed_file(tmp_path, monkeypatch):
    selected = tmp_path / "selected.ttf"
    selected.write_bytes(Path(font_info()["path"]).read_bytes())
    monkeypatch.setenv("SCIENTIFIC_FIGURE_FONT", str(selected))
    font_info.cache_clear()
    try:
        measure_text("PTL")
        selected.write_bytes(selected.read_bytes() + b"\x00")
        with pytest.raises(ValueError, match="selected font changed"):
            measure_text("PTL")
    finally:
        font_info.cache_clear()


def test_missing_glyph_fails_in_measurement_and_wrap():
    text = "PTL \U0010ffff"
    assert 0x10ffff not in font_info()["glyphs"]
    for operation in (lambda: measure_text(text), lambda: wrap_text(text, 100)):
        with pytest.raises(ValueError, match="no glyphs"):
            operation()


@pytest.mark.parametrize("width, kwargs", [(0, {}), (None, {}), (True, {}),
                                           (float("nan"), {}), (100, {"max_lines": True}),
                                           (100, {"max_lines": 257})])
def test_invalid_wrap_parameters(width, kwargs):
    with pytest.raises(ValueError):
        wrap_text("PTL", width, **kwargs)


def inventory():
    return {"schema_version": "0.1", "kind": "text_inventory", "id": "labels",
            "entries": [{"entry_id": "label1", "original": "PTL", "normalized": "PTL",
                         "suggestion": "PLT", "uncertainty": ["Abbreviation uncertain; preserve original"],
                         "source": {"image_sha256": "a" * 64,
                                    "coordinate_space": "exif-oriented-pixel-boundaries",
                                    "source_size": [96, 72], "roi": [10, 8, 60, 40],
                                    "bbox": [12, 10, 20, 10],
                                    "mappings": {"source_to_local": [1, 0, 0, 1, -10, -8],
                                                 "local_to_source": [1, 0, 0, 1, 10, 8]}},
                         "evidence_refs": [{"id": "evidence1", "sha256": "b" * 64}],
                         "resolution": "unresolved"}]}


def test_inventory_preserves_original_proposals_uncertainty_and_optional_binding():
    doc = inventory()
    before = copy.deepcopy(doc)
    result = validate_inventory(doc)
    assert result["valid"] and result["document"] == before == doc
    assert result["scene_mutation"] is False
    assert result["source_content_verified"] is False
    assert result["object_bindings_checked"] is False
    doc["entries"][0]["object_id"] = "label_object"
    bound = validate_inventory(doc, object_ids=["label_object"])
    assert bound["object_bindings_checked"] is True
    assert bound["document"]["entries"][0]["original"] == "PTL"
    bound["document"]["entries"][0]["original"] = "other"
    assert doc["entries"][0]["original"] == "PTL"
    with pytest.raises(ValueError, match="unknown"):
        validate_inventory(doc, object_ids=[])
    json.dumps(result, allow_nan=False)


def test_inventory_can_explicitly_hold_unbound_authored_text_and_null_proposals():
    doc = inventory()
    doc["entries"][0].update(source=None, normalized=None, suggestion=None, uncertainty=[])
    assert validate_inventory(doc)["valid"]
    doc["entries"][0]["resolution"] = "suggestion"
    with pytest.raises(ValueError, match="proposal is absent"):
        validate_inventory(doc)


@pytest.mark.parametrize("change", [
    lambda d: d["entries"][0].update(original=12),
    lambda d: d["entries"][0].update(uncertainty="maybe"),
    lambda d: d["entries"][0].pop("original"),
    lambda d: d["entries"][0]["source"].update(image_sha256="bad"),
    lambda d: d["entries"][0]["source"].update(coordinate_space="board"),
    lambda d: d["entries"][0]["source"].update(roi=[80, 8, 60, 40]),
    lambda d: d["entries"][0]["source"].update(bbox=[0, 0, 20, 10]),
    lambda d: d["entries"][0]["source"].update(source_size=[True, 72]),
    lambda d: d["entries"][0]["source"]["mappings"].update(local_to_source=[1, 0, 0, 1, 11, 8]),
    lambda d: d["entries"][0]["source"]["mappings"].update(source_to_local=[1, 0]),
    lambda d: d["entries"][0].update(evidence_refs=[{"id": "a", "sha256": "bad"}]),
    lambda d: d["entries"].append(copy.deepcopy(d["entries"][0])),
    lambda d: d["entries"][0].update(resolution="silently_rewritten"),
    lambda d: d.update(code="run_something()"),
])
def test_inventory_rejects_malformed_provenance_and_bindings(change):
    doc = inventory()
    change(doc)
    with pytest.raises(ValueError):
        validate_inventory(doc)


def test_legacy_text_manifest_is_not_reinterpreted_as_rich_inventory():
    old = {"schema_version": "0.1", "id": "text",
           "entries": [{"id": "label", "object_id": "obj", "text": "PTL"}]}
    with pytest.raises(ValueError, match="fields"):
        validate_inventory(old)

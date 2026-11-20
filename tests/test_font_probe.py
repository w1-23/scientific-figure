"""Task-level font probing: one real font, full character coverage, no promises."""
import json

from PIL import Image
import pytest

from scientific_figure.cli import execute, parser
from scientific_figure.fonts import (FONT_PROBE_LIMITS, font_candidates, font_info,
                                     probe_fonts, write_font_sample)


def test_active_font_covers_ascii_texts():
    report = probe_fonts(["Cell membrane", "H2O -> O2", "pH 7.4 (37 C)"])
    assert report["kind"] == "font_probe" and report["status"] == "ok"
    assert report["selected"]["activation"] == "active_font"
    assert report["selected"]["path"] == font_info()["path"]
    assert report["missing_count"] == 0
    assert report["single_font_rendering"] is True
    assert report["mixed_font_layout"] == "unsupported"
    assert any("fallback" in line for line in report["limitations"])


def test_unmapped_character_needs_input_and_is_reported():
    report = probe_fonts(["\U0010fffd"], candidates=[font_info()["path"]])
    assert report["status"] == "needs_input"
    assert report["selected"] is None
    assert "\U0010fffd" in report["missing_characters"]
    assert report["active_font"]["covers_all"] is False


def test_missing_candidate_is_reported_without_raising(tmp_path):
    report = probe_fonts(["\U0010fffd"], candidates=[str(tmp_path / "absent.ttf")])
    assert report["status"] == "needs_input"
    assert report["candidates"][0]["exists"] is False
    assert report["candidates"][0]["error"] == "missing file"


def test_candidate_that_covers_the_text_is_offered_only_as_activation():
    # A real font file from the host, chosen by the active font itself.
    report = probe_fonts(["Cell"], candidates=[font_info()["path"]])
    assert report["status"] == "ok"
    candidate = report["candidates"][0]
    assert candidate["covers_all"] is True and candidate["selectable"] is True
    assert candidate["faces"][0]["missing_count"] == 0


def test_cjk_needs_a_font_that_actually_covers_it():
    report = probe_fonts(["细胞膜"], candidates=[font_info()["path"]])
    if report["active_font"]["covers_all"]:
        assert report["status"] == "ok"
    else:
        assert report["status"] == "needs_input"
        assert report["missing_count"] == len(set("细胞膜"))


def test_probe_budgets_and_types_are_enforced():
    with pytest.raises(ValueError, match="nonempty list"):
        probe_fonts([])
    with pytest.raises(ValueError, match="string"):
        probe_fonts([3])
    with pytest.raises(ValueError, match="string"):
        probe_fonts(["ok", 3])
    with pytest.raises(ValueError, match="drawable"):
        probe_fonts(["   \n"])
    with pytest.raises(ValueError, match="character budget"):
        probe_fonts(["".join(chr(0x400 + i) for i in range(FONT_PROBE_LIMITS["characters"] + 1))])


def test_font_candidates_are_bounded_and_filtered(tmp_path):
    (tmp_path / "a.ttf").write_bytes(b"x")
    (tmp_path / "b.otf").write_bytes(b"x")
    (tmp_path / "c.txt").write_text("no", encoding="utf-8")
    found = font_candidates([tmp_path])
    assert [path.name for path in found] == ["a.ttf", "b.otf"]
    assert font_candidates([tmp_path], limit=1)[0].name == "a.ttf"


def test_sample_board_writes_a_png_with_the_requested_lines(tmp_path):
    output = tmp_path / "sample.png"
    info = write_font_sample(["细胞膜", "second line"], output, font_size=18)
    assert info["lines"] >= 7 and info["size"] == [960, Image.open(output).height]
    with Image.open(output) as image:
        assert image.format == "PNG" and image.size[0] == 960 and image.height > 0


def test_cli_font_probe_writes_report_and_sample(tmp_path):
    request = tmp_path / "texts.json"
    request.write_text(json.dumps({"texts": ["Cell membrane", "H2O -> O2"]}), encoding="utf-8")
    output = tmp_path / "probe"
    report = execute(parser().parse_args(["font-probe", str(request), "--output", str(output)]))
    written = json.loads((output / "font-probe.json").read_text(encoding="utf-8"))
    assert written["status"] == "ok" and written["selected"] == report["selected"]
    assert (output / "font-probe-sample.png").is_file()
    assert written["sample"]["path"].endswith("font-probe-sample.png")


def test_cli_font_probe_rejects_unknown_request_fields(tmp_path):
    request = tmp_path / "texts.json"
    request.write_text(json.dumps({"texts": ["Cell"], "extra": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="font-probe request"):
        execute(parser().parse_args(["font-probe", str(request)]))

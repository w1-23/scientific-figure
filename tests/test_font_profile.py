"""Font profile: packaged recommendation data, local resolution, CLI surface."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scientific_figure.cli import execute, parser
from scientific_figure.fonts import font_info, font_profile, resolve_profile


def test_packaged_profile_is_recommendation_only():
    profile = font_profile()
    assert profile["name"] == "scientific-default"
    assert profile["fallback"] == "single_font_only"
    assert profile["roles"]["text"]["latin"] == "Times New Roman"
    assert profile["roles"]["text"]["cjk"] == "Microsoft YaHei"
    assert profile["roles"]["math"]["family"] == "Cambria Math"


def test_unknown_profile_name_is_rejected():
    with pytest.raises(ValueError, match="unknown font profile"):
        font_profile("does-not-exist")


def test_missing_families_are_reported_not_substituted(tmp_path):
    report = resolve_profile(directories=[tmp_path], limit=8)
    assert sorted(report["missing"]) == ["Cambria Math", "Microsoft YaHei", "Times New Roman"]
    assert all(entry["available"] is False and entry["matches"] == [] for entry in report["roles"])
    assert report["rendering"] == "single_font_only"
    assert report["mixed_font_layout"] == "unsupported"


def test_installed_family_resolves_with_path_and_digest(tmp_path):
    source = Path(font_info()["path"])
    shutil.copy2(source, tmp_path / source.name)
    profile = {"name": "local", "fallback": "single_font_only",
               "roles": {"text": {"latin": font_info()["family"]}}}
    report = resolve_profile(profile, directories=[tmp_path], limit=8)
    assert report["missing"] == []
    entry = report["roles"][0]
    assert entry["available"] is True
    match = entry["matches"][0]
    assert Path(match["path"]).name == source.name
    assert len(match["sha256"]) == 64
    assert isinstance(match["sample_missing"], str)


def test_profile_is_part_of_the_cli_probe(tmp_path):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": "0.1", "texts": ["细胞 membrane"]},
                                  ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "out"
    report = execute(parser().parse_args(["font-probe", str(request), "--output", str(output),
                                          "--profile"]))
    assert report["profile"]["name"] == "scientific-default"
    assert report["profile"]["rendering"] == "single_font_only"
    written = json.loads((output / "font-probe.json").read_text(encoding="utf-8"))
    assert written["profile"]["roles"]

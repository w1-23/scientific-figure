"""Opt-in measurement cache: a hit skips recomputation, stale entries never serve."""
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

import scientific_figure.measurements as m
from scientific_figure.cli import execute, parser


def save_source(tmp_path, pixels=None, name="source.png"):
    pixels = np.arange(60 * 90 * 3, dtype=np.uint8).reshape(60, 90, 3) if pixels is None else np.asarray(pixels, dtype=np.uint8)
    path = tmp_path / name
    Image.fromarray(pixels).save(path)
    return path


def make_request(path, *, roi=None, question="What do these explicitly selected pixels show?"):
    with Image.open(path) as image:
        width, height = image.size
    return {"schema_version": "0.1",
            "source": {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
            "roi": roi or [0, 0, width, height], "question": question,
            "kind": "view", "background": None, "params": {}}


def run(tmp_path, request, name, cache_dir):
    return m.measure(request, tmp_path / name, base_directory=tmp_path, cache_dir=cache_dir)


def marker(path):
    return json.loads((path / "cache.json").read_text(encoding="utf-8"))


def forbid_recompute():
    def forbidden(*args, **kwargs):
        raise AssertionError("measurement recomputed instead of reading the cache")
    return forbidden


def test_cache_hit_republishes_without_recomputation(tmp_path, monkeypatch):
    src = save_source(tmp_path)
    request = make_request(src)
    cache = tmp_path / "cache"
    first = run(tmp_path, request, "first", cache)
    assert marker(tmp_path / "first") == {"schema_version": "0.1", "kind": "measurement_cache",
                                          "status": "miss", "cache_key": first["cache_key"]}
    assert [p.name for p in cache.iterdir()] == [first["cache_key"] + ".json"]
    monkeypatch.setattr(m, "_load_source", forbid_recompute())
    second = run(tmp_path, request, "second", cache)
    assert second == first
    assert marker(tmp_path / "second")["status"] == "hit"
    for name in ("result.json", "inspection_board.png"):
        assert (tmp_path / "second" / name).read_bytes() == (tmp_path / "first" / name).read_bytes()


def test_roi_or_question_change_is_a_miss(tmp_path):
    src = save_source(tmp_path)
    request = make_request(src)
    cache = tmp_path / "cache"
    run(tmp_path, request, "full", cache)
    run(tmp_path, make_request(src, roi=[10, 10, 40, 30]), "roi", cache)
    assert marker(tmp_path / "roi")["status"] == "miss"
    # The cache key ignores the question, so only the full request comparison
    # prevents an old provenance record from answering a different question.
    run(tmp_path, make_request(src, question="Did the platform change the pixels?"), "question", cache)
    assert marker(tmp_path / "question")["status"] == "miss"
    run(tmp_path, make_request(src, question="Did the platform change the pixels?"), "again", cache)
    assert marker(tmp_path / "again")["status"] == "hit"


def test_changed_source_bytes_are_refused_before_the_cache_is_consulted(tmp_path):
    src = save_source(tmp_path)
    request = make_request(src)
    cache = tmp_path / "cache"
    run(tmp_path, request, "first", cache)
    save_source(tmp_path, np.zeros((60, 90, 3), dtype=np.uint8))
    with pytest.raises(ValueError, match="source SHA256 mismatch"):
        run(tmp_path, request, "stale", cache)
    fresh = make_request(src)
    assert fresh["source"]["sha256"] != request["source"]["sha256"]
    run(tmp_path, fresh, "fresh", cache)
    assert marker(tmp_path / "fresh")["status"] == "miss"


@pytest.mark.parametrize("tamper", [
    lambda text: "{}",
    lambda text: json.dumps({**json.loads(text), "status": "ambiguous"}),
])
def test_corrupt_or_unsigned_entry_recomputes_and_is_replaced(tmp_path, tamper):
    src = save_source(tmp_path)
    request = make_request(src)
    cache = tmp_path / "cache"
    first = run(tmp_path, request, "first", cache)
    entry = cache / (first["cache_key"] + ".json")
    entry.write_text(tamper(entry.read_text(encoding="utf-8")), encoding="utf-8")
    run(tmp_path, request, "second", cache)
    assert marker(tmp_path / "second")["status"] == "miss"
    assert m.validate_measurement(json.loads(entry.read_text(encoding="utf-8"))) == first


def test_library_version_change_invalidates_the_entry(tmp_path, monkeypatch):
    src = save_source(tmp_path)
    request = make_request(src)
    cache = tmp_path / "cache"
    run(tmp_path, request, "first", cache)
    monkeypatch.setattr(m, "VERSIONS", {**m.VERSIONS, "numpy": "0.0-test"})
    run(tmp_path, request, "second", cache)
    assert marker(tmp_path / "second")["status"] == "miss"
    assert len(list(cache.iterdir())) == 2


def test_cache_is_off_by_default(tmp_path):
    src = save_source(tmp_path)
    request = make_request(src)
    result = m.measure(request, tmp_path / "plain", base_directory=tmp_path)
    assert sorted(p.name for p in (tmp_path / "plain").iterdir()) == ["inspection_board.png", "result.json"]
    assert not (tmp_path / "cache").exists()
    assert result["status"] == "ok"


def test_cli_cache_dir_hits_on_the_second_run(tmp_path, monkeypatch):
    src = save_source(tmp_path)
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(make_request(src)), encoding="utf-8")
    cache = tmp_path / "cache"

    def invoke(name):
        return execute(parser().parse_args(["evidence", "measure", str(request_path),
                                            "--output", str(tmp_path / name), "--cache-dir", str(cache)]))

    first = invoke("one")
    assert marker(tmp_path / "one")["status"] == "miss"
    monkeypatch.setattr(m, "_load_source", forbid_recompute())
    second = invoke("two")
    assert second == first
    assert marker(tmp_path / "two")["status"] == "hit"

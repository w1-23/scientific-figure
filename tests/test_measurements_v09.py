"""P3's file boundary, provenance, pixel assumptions and bounded algorithms."""
import base64
import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

import scientific_figure.measurements as m
from scientific_figure.geometry import point


def save_source(tmp_path, pixels, name="source.png", **kwargs):
    path = tmp_path / name
    Image.fromarray(np.asarray(pixels, dtype=np.uint8)).save(path, **kwargs)
    return path


def make_request(path, *, kind="view", roi=None, params=None, background=None, budget=None):
    with Image.open(path) as im:
        w, h = im.size
    request = {"schema_version": "0.1", "source": {"path": path.name,
               "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
               "roi": roi or [0, 0, w, h], "question": "What do these explicitly selected pixels show?",
               "kind": kind, "background": background, "params": params or {}}
    if budget is not None:
        request["budget"] = budget
    return request


def run(tmp_path, request, name="result"):
    return m.measure(request, tmp_path / name, base_directory=tmp_path)


def resign(result):
    result["result_sha256"] = m._digest({k: v for k, v in result.items() if k != "result_sha256"})
    return result


def test_view_file_interface_context_mapping_and_portable_inline_snapshot(tmp_path):
    pixels = np.arange(60 * 90 * 3, dtype=np.uint8).reshape(60, 90, 3)
    path = save_source(tmp_path, pixels)
    request = make_request(path, roi=[20, 15, 30, 20], params={"context_margin": 8, "max_side": 32})
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(request), encoding="utf-8")
    result = m.measure(request_file, tmp_path / "out")
    assert result["status"] == "ok"
    assert result["measurements"] == {"roi": [20, 15, 30, 20], "context_roi": [12, 7, 46, 36]}
    assert result["source"]["sha256"] == request["source"]["sha256"]
    assert result["mappings"] == {"source_to_local": [1, 0, 0, 1, -20, -15], "local_to_source": [1, 0, 0, 1, 20, 15]}
    assert point(result["mappings"]["source_to_local"], 24, 21) == (4, 6)
    for panel in result["board"]["panels"]:
        x, y, w, h = panel["source_roi"]
        bx, by, bw, bh = panel["rect"]
        assert point(panel["source_to_board"], x, y) == pytest.approx((bx, by))
        assert point(panel["source_to_board"], x + w, y + h) == pytest.approx((bx + bw, by + bh))
        assert point(panel["board_to_source"], bx, by) == pytest.approx((x, y))
    board = base64.b64decode(result["board"]["content"])
    assert board == (tmp_path / "out" / "inspection_board.png").read_bytes()
    assert json.loads((tmp_path / "out" / "result.json").read_text()) == result
    assert set(p.name for p in (tmp_path / "out").iterdir()) == {"result.json", "inspection_board.png"}
    path.unlink()
    request_file.unlink()
    (tmp_path / "out" / "inspection_board.png").unlink()
    validated = m.validate_measurement(result)
    assert validated == result
    validated["request"]["question"] = "changed"
    assert result["request"]["question"] != "changed"


def test_raw_alpha_rgb_points_lines_and_background_compositing(tmp_path):
    pixels = np.zeros((4, 7, 4), dtype=np.uint8)
    pixels[..., :3] = [200, 100, 50]
    pixels[..., 3] = np.arange(7, dtype=np.uint8) * 40
    path = save_source(tmp_path, pixels)
    params = {"points": [[1, 1], [5, 2]], "lines": [{"start": [1, 1], "end": [5, 2], "samples": 5}]}
    request = make_request(path, kind="profile", roi=[1, 1, 5, 2], params=params)
    raw = run(tmp_path, request, "raw")
    samples = raw["measurements"]["lines"][0]["samples"]
    assert [s["source"] for s in samples] == [[1, 1], [2, 1], [3, 2], [4, 2], [5, 2]]
    assert [s["alpha"] for s in samples] == [40, 80, 120, 160, 200]
    assert samples[0]["local"] == [0, 0]
    assert samples[0]["raw_rgb"] == samples[0]["rgb"] == [200, 100, 50]
    request["background"] = [255, 255, 255]
    composed = run(tmp_path, request, "composed")
    first = composed["measurements"]["points"][0]
    assert first["raw_rgb"] == [200, 100, 50] and first["alpha"] == 40
    assert first["rgb"] == [246, 231, 223]
    assert raw["cache_key"] != composed["cache_key"]
    assert raw["request"]["background"] is None
    assert composed["request"]["background"] == "#ffffff"


def test_topology_ring_boundary_and_threshold_sensitivity(tmp_path):
    pixels = np.full((13, 15, 3), 255, dtype=np.uint8)
    pixels[3:10, 4:11] = 60
    pixels[5:8, 6:9] = 255
    pixels[0:2, 0:2] = 0
    path = save_source(tmp_path, pixels)
    request = make_request(path, kind="topology", params={"thresholds": [50, 100, 150], "foreground": "dark"})
    result = run(tmp_path, request)
    assert result["status"] == "ambiguous"
    runs = result["measurements"]["runs"]
    assert [len(r["components"]) for r in runs] == [1, 2, 2]
    assert [len(r["hole_candidates"]) for r in runs] == [0, 1, 1]
    hole = runs[1]["hole_candidates"][0]
    assert hole == {"source_bbox": [6, 5, 9, 8], "local_bbox": [6, 5, 9, 8], "pixels": 9, "touches_roi_boundary": False}
    assert runs[1]["components"][0]["touches_roi_boundary"] is True
    sensitivity = result["measurements"]["sensitivity"]
    assert sensitivity["changed_pixels_between_thresholds"] == [40, 0]
    assert sensitivity["sensitive"] is True
    request["params"]["thresholds"] = [100]
    single = run(tmp_path, request, "single")
    assert single["status"] == "ok"
    assert any("unassessed" in s for s in single["limitations"])


def test_topology_dual_connectivity_and_light_foreground(tmp_path):
    pixels = np.zeros((3, 3, 3), dtype=np.uint8)
    pixels[0, 0] = pixels[1, 1] = 255
    path = save_source(tmp_path, pixels)
    for connectivity, components in ((4, 2), (8, 1)):
        req = make_request(path, kind="topology", params={"thresholds": [100], "foreground": "light", "connectivity": connectivity})
        result = run(tmp_path, req, f"c{connectivity}")
        assert len(result["measurements"]["runs"][0]["components"]) == components


def test_same_count_but_changed_threshold_pixels_remain_ambiguous(tmp_path):
    pixels = np.full((8, 8, 3), 255, dtype=np.uint8)
    pixels[2:6, 2:6] = 120
    pixels[3:5, 3:5] = 20
    path = save_source(tmp_path, pixels)
    req = make_request(path, kind="topology", params={"thresholds": [50, 150], "foreground": "dark"})
    result = run(tmp_path, req)
    assert [len(r["components"]) for r in result["measurements"]["runs"]] == [1, 1]
    assert result["measurements"]["sensitivity"]["changed_pixels_between_thresholds"] == [12]
    assert result["status"] == "ambiguous"


def repeat_source(tmp_path):
    rng = np.random.default_rng(42)
    pixels = rng.integers(0, 256, (24, 36, 3), dtype=np.uint8)
    tile = pixels[2:6, 2:7].copy()
    pixels[14:18, 22:27] = tile
    return save_source(tmp_path, pixels)


def test_repeat_same_source_single_scale_candidates_mapping_and_ambiguity(tmp_path):
    path = repeat_source(tmp_path)
    req = make_request(path, kind="repeat", roi=[1, 1, 33, 21], params={"template_roi": [2, 2, 5, 4], "threshold": 0.999})
    result = run(tmp_path, req)
    assert result["status"] == "ambiguous"
    values = result["measurements"]
    assert len(values["candidates"]) == 2
    assert {tuple(c["source_bbox"]) for c in values["candidates"]} == {(2, 2, 7, 6), (22, 14, 27, 18)}
    assert all(c["score"] > 0.999 for c in values["candidates"])
    assert values["raw_candidates"] == 2 and values["suppressed"] == 0
    assert values["ambiguity_reasons"]
    assert result["method"]["params"]["scale"] == 1


def test_template_can_be_outside_search_roi(tmp_path):
    path = repeat_source(tmp_path)
    req = make_request(path, kind="repeat", roi=[20, 12, 10, 9], params={"template_roi": [2, 2, 5, 4], "threshold": 0.99, "ambiguity_margin": 0})
    result = run(tmp_path, req)
    assert result["status"] == "ok"
    assert result["measurements"]["candidates"][0]["source_bbox"] == [22, 14, 27, 18]


def test_repeat_nms_reports_overlapping_placement_ambiguity(tmp_path):
    pixels = np.zeros((5, 12, 3), dtype=np.uint8)
    pixels[:] = np.arange(12, dtype=np.uint8)[None, :, None] * 15
    path = save_source(tmp_path, pixels)
    req = make_request(path, kind="repeat", params={"template_roi": [0, 0, 4, 5], "threshold": 0.99, "nms_iou": 0.2})
    result = run(tmp_path, req)
    values = result["measurements"]
    assert values["raw_candidates"] == 9
    assert values["suppressed"] > 0
    assert len(values["candidates"]) + values["suppressed"] == 9
    assert result["status"] == "ambiguous"
    assert values["candidates"][0]["local_bbox"] == [0, 0, 4, 5]


@pytest.mark.parametrize("scale,constant,reason", [(2, False, "scale"), (1, True, "Constant")])
def test_repeat_unsupported_scale_and_constant_template(tmp_path, scale, constant, reason):
    pixels = np.full((8, 9, 3), 100, dtype=np.uint8) if constant else np.random.default_rng(2).integers(0, 256, (8, 9, 3), dtype=np.uint8)
    path = save_source(tmp_path, pixels)
    req = make_request(path, kind="repeat", params={"template_roi": [0, 0, 3, 3], "threshold": 0.9, "scale": scale})
    result = run(tmp_path, req)
    assert result["status"] == "unsupported" and reason in result["measurements"]["reason"]


@pytest.mark.parametrize("kind", ["topology", "repeat"])
def test_transparency_without_background_is_unsupported(tmp_path, kind):
    pixels = np.zeros((8, 9, 4), dtype=np.uint8)
    pixels[..., 0] = np.arange(9, dtype=np.uint8)
    path = save_source(tmp_path, pixels)
    params = {"thresholds": [20], "foreground": "dark"} if kind == "topology" else {"template_roi": [0, 0, 3, 3], "threshold": 0.8}
    req = make_request(path, kind=kind, params=params)
    result = run(tmp_path, req)
    assert result["status"] == "unsupported"
    assert "background" in result["measurements"]["reason"]


def test_exif_oriented_source_pixel_indices(tmp_path):
    pixels = np.zeros((2, 3, 4), dtype=np.uint8)
    pixels[..., 3] = 255
    pixels[0, 0] = [100, 50, 20, 30]
    exif = Image.Exif()
    exif[274] = 6
    path = save_source(tmp_path, pixels, exif=exif)
    req = make_request(path, kind="profile", roi=[0, 0, 2, 3], params={"points": [[1, 0]], "lines": []})
    result = run(tmp_path, req)
    assert result["source"]["size"] == [2, 3]
    assert result["measurements"]["points"][0]["raw_rgb"] == [100, 50, 20]
    assert result["measurements"]["points"][0]["alpha"] == 30


def test_content_cache_stable_across_paths_and_invalidates_parameters(tmp_path):
    path = save_source(tmp_path, np.full((20, 20, 3), 190, dtype=np.uint8))
    req = make_request(path, roi=[3, 3, 5, 5], params={"context_margin": 0})
    first = run(tmp_path, req, "a")
    second = run(tmp_path, req, "b")
    assert first == second
    moved = tmp_path / "moved.png"
    moved.write_bytes(path.read_bytes())
    req["source"]["path"] = moved.name
    third = run(tmp_path, req, "c")
    assert third["cache_key"] == first["cache_key"]
    assert third["request_sha256"] != first["request_sha256"]
    req["params"]["context_margin"] = 1
    fourth = run(tmp_path, req, "d")
    assert fourth["cache_key"] != first["cache_key"]
    req["roi"] = [4, 3, 5, 5]
    fifth = run(tmp_path, req, "e")
    assert fifth["cache_key"] != fourth["cache_key"]


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(extra=1), lambda r: r.pop("background"),
    lambda r: r.update(roi=None), lambda r: r.update(roi=[-1, 0, 2, 2]),
    lambda r: r.update(roi=[0, 0, 99, 99]), lambda r: r.update(roi=[True, 0, 2, 2]),
    lambda r: r["source"].update(sha256="0" * 64), lambda r: r["source"].update(extra=1),
    lambda r: r.update(question=" "), lambda r: r["params"].update(unknown=2),
    lambda r: r.update(budget={"samples": 4097}), lambda r: r.update(budget={"new": 1}),
    lambda r: r.update(budget={"input_bytes": 1}), lambda r: r.update(budget={"input_pixels": 1}),
    lambda r: r.update(budget={"roi_pixels": 1}), lambda r: r.update(budget={"board_pixels": 1}),
    lambda r: r.update(budget={"derived_pixels": 1}), lambda r: r.update(budget={"result_bytes": 100}),
])
def test_invalid_requests_and_budgets_leave_no_output(tmp_path, mutate):
    path = save_source(tmp_path, np.zeros((6, 8, 3), dtype=np.uint8))
    req = make_request(path)
    mutate(req)
    with pytest.raises(ValueError):
        run(tmp_path, req)
    assert not (tmp_path / "result").exists()
    assert not list(tmp_path.glob(".result-*"))


@pytest.mark.parametrize("params", [
    {"points": [[8, 0]], "lines": []}, {"points": [], "lines": []},
    {"points": [], "lines": [{"start": [0, 0], "end": [1, 1], "samples": 4097}]},
    {"points": [[0, 0]], "lines": [{"start": [0, 0], "end": [1, 1], "samples": 4096}]},
])
def test_profile_out_of_bounds_and_aggregate_samples(tmp_path, params):
    path = save_source(tmp_path, np.zeros((6, 8, 3), dtype=np.uint8))
    with pytest.raises(ValueError):
        run(tmp_path, make_request(path, kind="profile", params=params))


def test_candidate_limits_before_nms_and_topology_fragmentation(tmp_path):
    pixels = np.indices((10, 10)).sum(axis=0) % 2 * 255
    path = save_source(tmp_path, np.repeat(pixels[..., None], 3, axis=2))
    req = make_request(path, kind="topology", params={"thresholds": [100], "foreground": "dark", "connectivity": 4}, budget={"candidates": 3})
    with pytest.raises(ValueError, match="candidates"):
        run(tmp_path, req)
    req = make_request(path, kind="repeat", params={"template_roi": [0, 0, 2, 2], "threshold": -1}, budget={"candidates": 3})
    with pytest.raises(ValueError, match="before NMS"):
        run(tmp_path, req)


def test_match_and_nms_work_limits_precede_algorithm(tmp_path, monkeypatch):
    path = repeat_source(tmp_path)
    req = make_request(path, kind="repeat", params={"template_roi": [2, 2, 5, 4], "threshold": 0.9}, budget={"match_work": 1})
    def unexpected(*args, **kwargs):
        pytest.fail("matchTemplate must not run over budget")
    original = m.cv2.matchTemplate
    monkeypatch.setattr(m.cv2, "matchTemplate", unexpected)
    with pytest.raises(ValueError, match="matching work"):
        run(tmp_path, req)
    monkeypatch.setattr(m.cv2, "matchTemplate", original)
    req["budget"] = {"nms_work": 1}
    with pytest.raises(ValueError, match="NMS work"):
        run(tmp_path, req)


def test_context_and_template_bounds_are_checked(tmp_path):
    path = save_source(tmp_path, np.zeros((50, 50, 3), dtype=np.uint8))
    req = make_request(path, roi=[20, 20, 5, 5], budget={"roi_pixels": 30})
    with pytest.raises(ValueError, match="context"):
        run(tmp_path, req)
    req = make_request(path, kind="repeat", roi=[0, 0, 10, 10], params={"template_roi": [49, 49, 2, 2], "threshold": 0.9})
    with pytest.raises(ValueError, match="outside"):
        run(tmp_path, req)


def test_duplicate_json_nonfinite_and_non_json_values_rejected(tmp_path):
    path = tmp_path / "bad.json"
    for data in ('{"schema_version":"0.1","schema_version":"0.1"}', '{"a":NaN}'):
        path.write_text(data)
        with pytest.raises(ValueError):
            m.measure(path, tmp_path / "out")
    with pytest.raises(ValueError):
        m.measure({"a": Path("bad")}, tmp_path / "out")


def test_atomic_publication_refuses_existing_and_cleans_failure(tmp_path, monkeypatch):
    path = save_source(tmp_path, np.zeros((6, 8, 3), dtype=np.uint8))
    req = make_request(path)
    (tmp_path / "existing").mkdir()
    (tmp_path / "existing" / "sentinel").write_text("keep")
    with pytest.raises(FileExistsError):
        run(tmp_path, req, "existing")
    assert (tmp_path / "existing" / "sentinel").read_text() == "keep"
    import scientific_figure.evidence as ev
    def fail(stage, target):
        assert (stage / "result.json").is_file()
        raise OSError("injected publication failure")
    monkeypatch.setattr(ev, "_rename_new", fail)
    with pytest.raises(OSError, match="injected"):
        run(tmp_path, req)
    assert not (tmp_path / "result").exists()
    assert not list(tmp_path.glob(".result-*"))


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(scene={}), lambda r: r.update(status="semantic_success"),
    lambda r: r["mappings"]["source_to_local"].__setitem__(4, 2),
    lambda r: r["source"].update(sha256="0" * 64),
    lambda r: r["board"]["panels"][0]["source_to_board"].__setitem__(0, 9),
    lambda r: r["board"].update(content="AAAA"),
    lambda r: r["method"]["params"].update(max_side=400),
    lambda r: r.update(request_sha256="0" * 64),
    lambda r: r.update(cache_key="0" * 64),
    lambda r: r.update(limitations=[]),
    lambda r: r.update(derived_pixels=1),
    lambda r: r.update(status="ambiguous"),
])
def test_validator_rejects_tampering_even_with_recomputed_result_hash(tmp_path, mutate):
    path = save_source(tmp_path, np.zeros((8, 9, 3), dtype=np.uint8))
    result = run(tmp_path, make_request(path))
    mutate(result)
    with pytest.raises(ValueError):
        m.validate_measurement(resign(result))


def test_validator_rejects_pixel_record_and_digest_changes(tmp_path):
    path = save_source(tmp_path, np.zeros((8, 9, 3), dtype=np.uint8))
    result = run(tmp_path, make_request(path, kind="profile", params={"points": [[0, 0]], "lines": []}))
    wrong_hash = copy.deepcopy(result)
    wrong_hash["measurements"]["points"][0]["alpha"] = 1
    with pytest.raises(ValueError):
        m.validate_measurement(wrong_hash)
    result["measurements"]["points"][0]["local"] = [2, 2]
    with pytest.raises(ValueError, match="source/local"):
        m.validate_measurement(resign(result))


def test_snapshot_validation_uses_recorded_versions_and_normalizes_bad_png_error(tmp_path, monkeypatch):
    path = save_source(tmp_path, np.zeros((8, 9, 3), dtype=np.uint8))
    result = run(tmp_path, make_request(path))
    monkeypatch.setattr(m, "VERSIONS", {"numpy": "future", "opencv": "future", "pillow": "future"})
    assert m.validate_measurement(result) == result
    malformed = b"not a PNG"
    result["board"]["content"] = base64.b64encode(malformed).decode("ascii")
    result["board"]["sha256"] = hashlib.sha256(malformed).hexdigest()
    with pytest.raises(ValueError, match="invalid inline board PNG"):
        m.validate_measurement(resign(result))

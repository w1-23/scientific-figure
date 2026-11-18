"""CPU evidence and explicit preparation contracts, including hostile receipts."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import zlib

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest

import scientific_figure.evidence as ev
from scientific_figure.evidence import generate_evidence, validate_evidence
from scientific_figure.geometry import point
from scientific_figure.preparation import prepare
from scientific_figure.scene import load_json, schema_validate


def source(tmp_path, name="input.png", size=(96, 72)):
    path = tmp_path / name
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((size[0] // 4, size[1] // 4, size[0] * 3 // 4, size[1] * 3 // 4), fill="#204080")
    draw.line((0, size[1] // 2, size[0] - 1, size[1] // 2), fill="black", width=2)
    image.save(path)
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path, data):
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")


def rewrite_evidence(root, edit):
    evidence = load_json(root / "evidence.json")
    manifest = load_json(root / "manifest.json")
    edit(evidence, manifest)
    save_json(root / "evidence.json", evidence)
    record = next(r for r in manifest["artifacts"] if r["path"] == "evidence.json")
    record.update(sha256=digest(root / "evidence.json"), bytes=(root / "evidence.json").stat().st_size)
    save_json(root / "manifest.json", manifest)


def test_default_preserves_original_and_returns_single_receipt(tmp_path):
    original = source(tmp_path)
    before = original.read_bytes(), original.stat().st_mtime_ns
    output = tmp_path / "evidence"
    receipt = generate_evidence(original, output)
    assert isinstance(receipt, dict) and receipt["valid"] is True
    assert validate_evidence(output) == receipt
    assert receipt["channels"] == ["contrast"]
    assert all(Path(receipt[k]).is_file() for k in ("evidence_path", "manifest_path", "board_path", "source_path"))
    assert (original.read_bytes(), original.stat().st_mtime_ns) == before
    evidence = load_json(output / "evidence.json")
    manifest = load_json(output / "manifest.json")
    schema_validate(evidence, "evidence")
    schema_validate(manifest, "evidence-manifest")
    assert evidence["source"]["sha256"] == digest(original)
    assert set(p.name for p in output.iterdir()) == {
        "source.png", "contrast_gradient.png", "contrast_edges.png", "contrast_overlay.png",
        "evidence_board.png", "evidence.json", "manifest.json"}
    assert np.count_nonzero(np.asarray(Image.open(output / "contrast_edges.png"))) > 0
    assert np.count_nonzero(np.asarray(Image.open(output / "contrast_gradient.png"))) > 0
    # Directory validation is portable and does not need the original image again.
    original.unlink()
    assert validate_evidence(output)["valid"]


@pytest.mark.parametrize("channels", [["detail"], ["palette"], ["palette", "contrast"],
                                      ["contrast", "detail", "palette"]])
def test_only_requested_channels_and_deterministic_rasters(tmp_path, channels):
    original = source(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    generate_evidence(original, a, channels=channels)
    generate_evidence(original, b, channels=list(reversed(channels)))
    evidence = load_json(a / "evidence.json")
    assert evidence["channels"] == [c for c in ev.CHANNELS if c in channels]
    for channel in ev.CHANNELS:
        assert bool(list(a.glob(f"{channel}*.png"))) == (channel in channels)
    for image in a.glob("*.png"):
        assert image.read_bytes() == (b / image.name).read_bytes()
    assert evidence["palette"] == load_json(b / "evidence.json")["palette"]
    assert evidence["derived_pixels"] <= 16_000_000
    assert np.prod(Image.open(a / "evidence_board.png").size) <= 2_000_000
    if "palette" in channels:
        assert sum(p["pixels"] for p in evidence["palette"]) == 96 * 72
        assert sum(p["fraction"] for p in evidence["palette"]) == pytest.approx(1)


@pytest.mark.parametrize("channels", [["contrast"], ["contrast", "detail", "palette"]])
def test_small_board_fits_thumbnails_and_label_ink_without_upscaling(tmp_path, channels):
    original = source(tmp_path, size=(47, 35))
    output = tmp_path / "compact"
    generate_evidence(original, output, channels=channels)
    evidence = load_json(output / "evidence.json")
    with Image.open(output / "evidence_board.png") as raster:
        board = raster.copy()
    panels = evidence["panels"]
    columns = min(3, len(panels))
    rows = (len(panels) + columns - 1) // columns
    old_area = columns * 384 * rows * (384 + 24)
    assert board.width * board.height < old_area // 4
    assert board.width < columns * 384
    assert board.height < rows * (384 + 24)
    assert panels[0]["rect"][2:] == [47, 35]
    assert panels[0]["artifact_to_board"][:4] == [1, 0, 0, 1]
    font = ImageFont.load_default()
    draw = ImageDraw.Draw(board)
    for index, panel in enumerate(panels):
        name = panel["artifact"]
        x, image_y, width, height = panel["rect"]
        assert [width, height] == ([256, 64] if name == "palette.png" else [47, 35])
        left, top, right, bottom = font.getbbox(name)
        label_height = max(24, bottom - top + 8)
        row_y = image_y - label_height
        cell_right = panels[index + 1]["rect"][0] if index % columns < columns - 1 and index + 1 < len(panels) else board.width
        ink = draw.textbbox((x + 4 - left, row_y + 4 - top), name, font=font)
        assert x + 4 <= ink[0] < ink[2] <= cell_right - 4
        assert row_y + 4 <= ink[1] < ink[3] <= image_y - 4
        label_pixels = np.asarray(board.crop((x, row_y, cell_right, image_y)))
        visible_y, visible_x = np.nonzero(np.any(label_pixels != [238, 238, 238], axis=2))
        assert len(visible_x) > 0
        assert visible_x.min() >= 4 and visible_x.max() < cell_right - x - 4
        assert visible_y.min() >= 4 and visible_y.max() < label_height - 4
        assert point(panel["board_to_artifact"], *point(panel["artifact_to_board"], 47, 35)) == pytest.approx((47, 35))
    source_rect = panels[0]["rect"]
    x, y, w, h = source_rect
    assert board.crop((x, y, x + w, y + h)).tobytes() == Image.open(output / "source.png").tobytes()
    assert validate_evidence(output)["valid"]


def test_board_validator_rejects_padded_dimensions_even_with_repaired_hashes(tmp_path):
    original = source(tmp_path, size=(47, 35))
    output = tmp_path / "compact"
    generate_evidence(original, output, channels=["contrast", "detail", "palette"])
    board_path = output / "evidence_board.png"
    with Image.open(board_path) as board:
        old_area = board.width * board.height
        padded = Image.new("RGB", (1152, 1224), "#eeeeee")
        padded.paste(board, (0, 0))
    padded.save(board_path)
    def update(evidence, manifest):
        for records in (evidence["artifacts"], manifest["artifacts"]):
            record = next(r for r in records if r["path"] == "evidence_board.png")
            record.update(sha256=digest(board_path), bytes=board_path.stat().st_size,
                          size=list(padded.size))
        evidence["derived_pixels"] += padded.width * padded.height - old_area
    rewrite_evidence(output, update)
    with pytest.raises(ValueError, match="board dimensions"):
        validate_evidence(output)


def test_hidden_rgb_never_produces_an_edge(tmp_path):
    for index in range(2):
        pixels = np.zeros((40, 60, 4), dtype=np.uint8)
        pixels[:, :30, :3] = [255, 0, 0] if index else [0, 0, 255]
        pixels[:, 30:, :3] = [0, 255, 0] if index else [255, 255, 255]
        original = tmp_path / f"alpha{index}.png"
        Image.fromarray(pixels).save(original)
        before = digest(original)
        if index == 0:
            with pytest.raises(ValueError, match="background"):
                generate_evidence(original, tmp_path / "missing-background")
        output = tmp_path / f"out{index}"
        generate_evidence(original, output, channels=["contrast", "detail", "palette"], background="#d0d0d0")
        assert digest(original) == before
        for channel in ("contrast", "detail"):
            assert not np.any(np.asarray(Image.open(output / f"{channel}_edges.png")))
            assert not np.any(np.asarray(Image.open(output / f"{channel}_gradient.png")))
        assert Image.open(output / "source.png").getpixel((10, 10)) == (208, 208, 208)
    for name in ("source.png", "contrast_edges.png", "detail_edges.png", "palette.png"):
        assert (tmp_path / "out0" / name).read_bytes() == (tmp_path / "out1" / name).read_bytes()
    assert not (tmp_path / "missing-background").exists()
    assert not list(tmp_path.glob(".missing-background-*"))


def test_partial_alpha_composites_before_diagnostics(tmp_path):
    original = tmp_path / "alpha.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 128)).save(original)
    generate_evidence(original, tmp_path / "out", background=[0, 0, 255])
    assert Image.open(tmp_path / "out" / "source.png").getpixel((0, 0)) == (128, 0, 127)
    evidence = load_json(tmp_path / "out" / "evidence.json")
    assert evidence["source"]["alpha_composited"]
    assert evidence["source"]["background"] == "#0000ff"


def test_roi_and_all_six_coordinate_maps_round_trip(tmp_path):
    original = source(tmp_path, size=(900, 950))
    output = tmp_path / "roi"
    roi = [101, 73, 701, 801]
    generate_evidence(original, output, region=roi)
    evidence = load_json(output / "evidence.json")
    assert evidence["source"]["roi"] == roi
    with Image.open(original) as image:
        assert Image.open(output / "source.png").tobytes() == image.crop((101, 73, 802, 874)).tobytes()
    maps = evidence["mappings"]
    for forward, backward in [("source_to_local", "local_to_source"),
                              ("source_to_board", "board_to_source"),
                              ("local_to_board", "board_to_local")]:
        for xy in [(0, 0), (101, 73), (802, 874), (49.25, 83.75)]:
            assert point(maps[backward], *point(maps[forward], *xy)) == pytest.approx(xy)
    assert point(maps["source_to_local"], 101, 73) == (0, 0)
    panel = evidence["panels"][0]
    assert point(maps["source_to_board"], 101, 73) == tuple(panel["rect"][:2])
    assert point(maps["source_to_board"], 802, 874) == pytest.approx(
        (panel["rect"][0] + panel["rect"][2], panel["rect"][1] + panel["rect"][3]))


def test_exif_orientation_precedes_roi_and_is_not_reapplied(tmp_path):
    original = tmp_path / "oriented.jpg"
    image = Image.new("RGB", (12, 8), "white")
    ImageDraw.Draw(image).rectangle((0, 0, 5, 7), fill="black")
    exif = Image.Exif()
    exif[274] = 6
    image.save(original, exif=exif)
    before = digest(original)
    output = tmp_path / "out"
    generate_evidence(original, output, region=[1, 2, 5, 9])
    evidence = load_json(output / "evidence.json")
    assert evidence["source"]["raw_size"] == [12, 8]
    assert evidence["source"]["size"] == [8, 12]
    assert evidence["source"]["exif_orientation"] == 6
    derived = Image.open(output / "source.png")
    assert derived.size == (5, 9) and not derived.getexif()
    from PIL import ImageOps
    with Image.open(original) as raw:
        assert derived.tobytes() == ImageOps.exif_transpose(raw).crop((1, 2, 6, 11)).convert("RGB").tobytes()
    assert digest(original) == before


@pytest.mark.parametrize("kwargs", [
    {"channels": []}, {"channels": ["skeleton"]}, {"channels": ["contrast", "contrast"]},
    {"channels": "contrast"}, {"region": [-1, 0, 10, 10]}, {"region": [0, 0, 0, 10]},
    {"region": [90, 0, 10, 10]}, {"region": [True, 0, 10, 10]},
    {"region": [0, 0, 10.5, 10]}, {"background": "transparent"},
    {"background": [0, True, 255]},
])
def test_invalid_arguments_leave_no_output(tmp_path, kwargs):
    original = source(tmp_path)
    with pytest.raises(ValueError):
        generate_evidence(original, tmp_path / "out", **kwargs)
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".out-*"))


def test_input_byte_pixel_and_roi_budgets(tmp_path):
    large_bytes = tmp_path / "large.bin"
    with large_bytes.open("wb") as stream:
        stream.seek(32 * 1024 * 1024)
        stream.write(b"x")
    with pytest.raises(ValueError, match="byte budget"):
        generate_evidence(large_bytes, tmp_path / "byte-out")
    original = source(tmp_path)
    # Valid PNG header announces >40MP; budget must fail before IDAT decoding.
    png = bytearray(original.read_bytes())
    png[16:24] = struct.pack(">II", 6500, 6200)
    png[29:33] = struct.pack(">I", zlib.crc32(png[12:29]))
    huge = tmp_path / "huge.png"
    huge.write_bytes(png)
    with pytest.raises(ValueError, match="input pixel budget"):
        generate_evidence(huge, tmp_path / "pixel-out", region=[0, 0, 10, 10])
    roi_source = tmp_path / "roi.png"
    Image.new("RGB", (1001, 1000)).save(roi_source)
    with pytest.raises(ValueError, match="ROI exceeds"):
        generate_evidence(roi_source, tmp_path / "roi-out")
    generate_evidence(roi_source, tmp_path / "bounded", region=[1, 0, 1000, 1000], channels=["palette"])
    assert validate_evidence(tmp_path / "bounded")["valid"]
    assert all(not (tmp_path / name).exists() for name in ("byte-out", "pixel-out", "roi-out"))


@pytest.mark.parametrize("limit", ["MAX_BOARD_PIXELS", "MAX_DERIVED_PIXELS"])
def test_board_and_derived_budgets_cleanup(tmp_path, monkeypatch, limit):
    original = source(tmp_path)
    monkeypatch.setattr(ev, limit, 10)
    with pytest.raises(ValueError, match="pixel budget"):
        generate_evidence(original, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".out-*"))


def test_existing_and_concurrently_created_outputs_are_never_replaced(tmp_path, monkeypatch):
    original = source(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(FileExistsError):
        generate_evidence(original, output)
    assert list(output.iterdir()) == []
    output.rmdir()
    rename = ev._rename_new
    def race(stage, target):
        target.mkdir()
        (target / "owner.txt").write_text("concurrent owner")
        rename(stage, target)
    monkeypatch.setattr(ev, "_rename_new", race)
    with pytest.raises(OSError):
        generate_evidence(original, output)
    assert (output / "owner.txt").read_text() == "concurrent owner"
    assert not list(tmp_path.glob(".out-*"))


@pytest.mark.parametrize("corruption", ["hash", "path", "manifest_path", "map", "pixel_total",
                                        "roi", "extra", "missing", "parameter", "duplicate"])
def test_detects_corruption_even_with_metadata_hash_repaired(tmp_path, corruption):
    original = source(tmp_path)
    output = tmp_path / "out"
    generate_evidence(original, output)
    if corruption == "hash":
        path = output / "contrast_edges.png"
        pixels = np.asarray(Image.open(path)).copy()
        pixels[0, 0] ^= 255
        Image.fromarray(pixels).save(path)
    elif corruption == "extra":
        (output / "extra.png").write_bytes(b"extra")
    elif corruption == "missing":
        (output / "source.png").unlink()
    else:
        def corrupt(evidence, manifest):
            if corruption == "path":
                evidence["artifacts"][0]["path"] = "../source.png"
            elif corruption == "manifest_path":
                manifest["artifacts"][0]["path"] = "C:\\escape.png"
            elif corruption == "map":
                evidence["mappings"]["source_to_board"] = [0, 0, 0, 0, 0, 0]
            elif corruption == "pixel_total":
                evidence["derived_pixels"] += 1
            elif corruption == "roi":
                evidence["source"]["roi"] = [0, 0, 100, 100]
            elif corruption == "parameter":
                evidence["parameters"]["algorithm"] = "model-auto-route"
            elif corruption == "duplicate":
                evidence["artifacts"][1] = copy.deepcopy(evidence["artifacts"][0])
        rewrite_evidence(output, corrupt)
    with pytest.raises(ValueError):
        validate_evidence(output)


def test_symlink_artifact_reference_is_rejected_where_supported(tmp_path):
    original = source(tmp_path)
    output = tmp_path / "out"
    generate_evidence(original, output)
    source_png = output / "source.png"
    replacement = tmp_path / "replacement.png"
    replacement.write_bytes(source_png.read_bytes())
    source_png.unlink()
    try:
        source_png.symlink_to(replacement)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(ValueError, match="symlink"):
        validate_evidence(output)


def test_strict_schemas_and_inline_evidence_without_external_source_path(tmp_path):
    original = source(tmp_path)
    output = tmp_path / "out"
    generate_evidence(original, output)
    evidence = load_json(output / "evidence.json")
    evidence["source"].pop("path")
    schema_validate(evidence, "evidence")
    evidence["source"]["unknown"] = "rejected"
    with pytest.raises(ValueError, match="Additional properties"):
        schema_validate(evidence, "evidence")


def test_prepare_multiple_relative_inputs_and_manifest_bindings(tmp_path, monkeypatch):
    request_dir = tmp_path / "requests"
    request_dir.mkdir()
    a = source(request_dir, "first.png")
    b = request_dir / "second.png"
    Image.new("RGBA", (32, 24), (20, 30, 40, 128)).save(b)
    request = {"schema_version": "0.1", "task": "asset_bundle", "background": "#ffffff",
               "inputs": [{"id": "a", "path": "first.png", "roi": [2, 3, 40, 30], "channels": ["detail"]},
                          {"id": "b", "path": "second.png", "channels": ["palette"]}]}
    request_path = request_dir / "request.json"
    save_json(request_path, request)
    a_hash, b_hash = digest(a), digest(b)
    # Deliberately change cwd: input paths still resolve against request directory.
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "prepared"
    receipt = prepare(request_path, output)
    assert receipt["preflight_status"] == "not_integrated"
    assert all(Path(r["evidence_path"]).is_file() for r in receipt["inputs"])
    manifest = load_json(output / "input-manifest.json")
    schema_validate(manifest, "preparation-manifest")
    assert manifest["preflight_status"] == "not_integrated"
    assert manifest["request"]["sha256"] == digest(request_path)
    for entry, expected in zip(manifest["inputs"], [a_hash, b_hash]):
        assert entry["source_sha256"] == expected
        assert digest(output / entry["evidence"]) == entry["evidence_sha256"]
        assert digest(output / entry["manifest"]) == entry["manifest_sha256"]
        assert validate_evidence((output / entry["evidence"]).parent)["valid"]
    assert digest(a) == a_hash and digest(b) == b_hash
    assert manifest["inputs"][0]["roi"] == [2, 3, 40, 30]
    assert not list(tmp_path.glob(".prepared-*"))


def test_prepare_single_and_failed_bundle_are_atomic(tmp_path):
    original = source(tmp_path)
    request_path = tmp_path / "request.json"
    one = {"schema_version": "0.1", "task": "single_asset", "inputs": [{"id": "asset", "path": original.name}]}
    save_json(request_path, one)
    assert prepare(request_path, tmp_path / "single")["task"] == "single_asset"
    with pytest.raises(FileExistsError):
        prepare(request_path, tmp_path / "single")
    broken = copy.deepcopy(one)
    broken["task"] = "asset_bundle"
    broken["inputs"].append({"id": "missing", "path": "absent.png"})
    save_json(request_path, broken)
    with pytest.raises(FileNotFoundError):
        prepare(request_path, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".failed-*"))


@pytest.mark.parametrize("edit", ["routing", "duplicate_id", "traversal_id", "single_multiple", "reserved_id"])
def test_prepare_rejects_implicit_routing_and_invalid_requests(tmp_path, edit):
    original = source(tmp_path)
    request = {"schema_version": "0.1", "task": "single_asset", "inputs": [{"id": "asset", "path": original.name}]}
    if edit == "routing":
        request["model"] = "auto"
    elif edit == "duplicate_id":
        request["task"] = "asset_bundle"
        request["inputs"].append({"id": "ASSET", "path": original.name})
    elif edit == "traversal_id":
        request["inputs"][0]["id"] = "../escape"
    elif edit == "single_multiple":
        request["inputs"].append({"id": "other", "path": original.name})
    else:
        request["inputs"][0]["id"] = "CON"
    path = tmp_path / "request.json"
    save_json(path, request)
    with pytest.raises(ValueError):
        prepare(path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_prepare_duplicate_json_keys_are_rejected(tmp_path):
    request = tmp_path / "request.json"
    request.write_text('{"schema_version":"0.1","task":"single_asset","task":"asset_bundle","inputs":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        prepare(request, tmp_path / "out")

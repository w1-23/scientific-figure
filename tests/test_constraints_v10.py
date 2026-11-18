"""Constraint packs and boundary checks: evidence must gate, not decorate."""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scientific_figure.builder import SceneBuilder
from scientific_figure.constraints import (build_constraints, check_constraints,
                                           review_checks, validate_constraint_pack)
from scientific_figure.coords import frame_matrix, load_oriented, source_to_scene
from scientific_figure.project import write_project

WIDTH, HEIGHT = 320, 220
RING = {"cx": 160.0, "cy": 110.0, "rx": 70.0, "ry": 50.0}


def _source(path, *, offset=0.0, dots=5, hole=True):
    import cv2
    canvas = np.full((HEIGHT, WIDTH, 3), 255, np.uint8)
    cv2.ellipse(canvas, (int(RING["cx"] + offset), int(RING["cy"])),
                (70, 50), 0, 0, 360, (20, 40, 80), 5)
    for index in range(dots):
        cv2.circle(canvas, (40 + index * 22, 190), 6, (200, 60, 40), -1)
    if hole:
        cv2.rectangle(canvas, (240, 30), (300, 90), (30, 30, 30), -1)
        cv2.rectangle(canvas, (258, 48), (282, 72), (255, 255, 255), -1)
    Image.fromarray(canvas).save(path)
    return path


def _project(directory, *, offset=0.0):
    builder = SceneBuilder(WIDTH, HEIGHT, background="#ffffff", schema_version="0.3")
    builder.group("figure")
    builder.ellipse("ring", RING["cx"] + offset, RING["cy"], RING["rx"], RING["ry"],
                    parent="figure", style={"fill": "none", "stroke": "#142850", "stroke_width": 5})
    for index in range(5):
        builder.ellipse(f"dot-{index}", 40 + index * 22, 190, 6, 6, parent="figure",
                        style={"fill": "#c83c28"})
    builder.rect("block", 240, 30, 60, 60, parent="figure", style={"fill": "#1e1e1e"})
    builder.ellipse("hole", 270, 60, 12, 12, parent="figure", style={"fill": "#ffffff"})
    write_project(builder.build(), directory, scale=1)
    return directory


def _bindings(**overrides):
    binding = {"object_id": "ring", "kind": "boundary", "source_region": [84, 54, 152, 112],
               "allowed_deviation": 3.0, "recall_min": 0.6}
    binding.update(overrides)
    return [binding]


def test_pack_is_bounded_typed_and_repeat_candidates_are_real(tmp_path):
    source = _source(tmp_path / "reference.png")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": "0.1", "background": "#ffffff"}),
                       encoding="utf-8")
    pack = build_constraints(source, request, tmp_path / "pack")
    assert pack["edge_packs"]["medium"]["components"]
    assert any(c["class"] == "strong_structural"
               for c in pack["edge_packs"]["medium"]["components"])
    assert pack["holes"], "a filled block with a white centre must yield a hole candidate"
    groups = [g for g in pack["repetitions"] if g["count"] >= 5]
    assert groups, "five equally spaced dots must form a repetition candidate"
    assert groups[0]["median_spacing"] > 10
    with pytest.raises(FileExistsError):
        build_constraints(source, request, tmp_path / "pack")


def test_boundary_check_passes_on_aligned_render_and_fails_on_offset(tmp_path):
    source = _source(tmp_path / "reference.png")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": "0.1", "background": "#ffffff"}),
                       encoding="utf-8")
    pack_dir = tmp_path / "pack"
    build_constraints(source, request, pack_dir)
    project = _project(tmp_path / "project")
    before = (project / "scene.json").read_bytes()
    report = check_constraints(project, pack_dir, bindings=_bindings())
    assert report["kind"] == "constraint_check"
    assert report["bindings"][0]["status"] == "passed", report["bindings"][0]
    assert report["bindings"][0]["metrics"]["recall"] > 0.6
    assert (project / "scene.json").read_bytes() == before, "checking must not edit the project"

    shifted = _project(tmp_path / "shifted", offset=10.0)
    bad = check_constraints(shifted, pack_dir, bindings=_bindings())
    assert bad["bindings"][0]["status"] in {"needs_review", "failed"}
    assert bad["bindings"][0]["metrics"]["mean_offset"] > 3.0


def test_negative_space_and_repetition_bindings_are_checked(tmp_path):
    source = _source(tmp_path / "reference.png")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": "0.1", "background": "#ffffff"}),
                       encoding="utf-8")
    pack_dir = tmp_path / "pack"
    pack = build_constraints(source, request, pack_dir)
    group = next(g for g in pack["repetitions"] if g["count"] >= 5)
    project = _project(tmp_path / "project")
    report = check_constraints(project, pack_dir, bindings=[
        {"object_id": "block", "kind": "negative_space", "source_region": [230, 20, 80, 80],
         "allowed_deviation": 4, "recall_min": 0.4},
        {"object_id": "figure", "kind": "repetition", "source_region": [20, 170, 140, 40],
         "allowed_deviation": 4, "recall_min": 0.4, "group": group["id"], "min_count": 5}])
    kinds = {entry["kind"]: entry for entry in report["bindings"]}
    assert kinds["repetition"]["detail"]["detected"] >= 5
    assert kinds["negative_space"]["status"] in {"passed", "needs_review"}


def test_review_checks_never_silently_pass_a_failed_binding(tmp_path):
    source = _source(tmp_path / "reference.png")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": "0.1", "background": "#ffffff"}),
                       encoding="utf-8")
    pack_dir = tmp_path / "pack"
    build_constraints(source, request, pack_dir)
    shifted = _project(tmp_path / "shifted", offset=12.0)
    report = check_constraints(shifted, pack_dir, bindings=_bindings(recall_min=0.9))
    checks = review_checks(report)
    assert checks and checks[0]["status"] in {"needs_review", "failed"}
    assert report["visual_review"] == "host_required"
    assert report["scientific_review"] == "not_executed"
    with pytest.raises(ValueError):
        review_checks({"kind": "scene"})


def test_coordinate_contract_and_exif_orientation(tmp_path):
    image = Image.new("RGB", (40, 20), "white")
    image.putpixel((0, 0), (255, 0, 0))
    exif = Image.Exif()
    exif[274] = 6  # rotate 90 degrees clockwise
    path = tmp_path / "rotated.jpg"
    image.save(path, exif=exif)
    loaded, meta = load_oriented(path)
    assert meta["raw_size"] == [40, 20] and meta["size"] == [20, 40]
    assert meta["exif_orientation"] == 6
    assert frame_matrix({"origin": [10, 20], "scale": 2, "rotation": 0}) == [2, 0, 0, 2, 10, 20]
    with pytest.raises(ValueError):
        frame_matrix({"origin": [0, 0], "scale": 0})
    with pytest.raises(ValueError):
        frame_matrix({"origin": [0, 0], "shear": 1})
    matrix = source_to_scene({"width": 100, "height": 100}, [10, 10, 40, 20])
    x, y = matrix[0] * 10 + matrix[2] * 10 + matrix[4], matrix[1] * 10 + matrix[3] * 10 + matrix[5]
    assert (round(x), round(y)) == (0, 25)


def test_pack_validation_detects_tampering(tmp_path):
    source = _source(tmp_path / "reference.png")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": "0.1", "background": "#ffffff"}),
                       encoding="utf-8")
    pack_dir = tmp_path / "pack"
    build_constraints(source, request, pack_dir)
    validate_constraint_pack(pack_dir)
    (pack_dir / "normalized.png").write_bytes(b"not a png")
    with pytest.raises(ValueError):
        validate_constraint_pack(pack_dir)

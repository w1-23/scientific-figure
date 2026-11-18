"""The cross-domain suite must keep detecting lost internal structure."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples" / "structural-regression" / "run.py"


def _suite():
    spec = importlib.util.spec_from_file_location("structural_regression", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_case_reports_its_designed_outcome(tmp_path):
    module = _suite()
    outcomes = {}
    for name, case in module.CASES.items():
        record = module.run_case(name, case, tmp_path, None)
        outcomes[name] = record
        assert record["ok"], record
    assert outcomes["materials-layered"]["variants"]["degraded"]["overall"] == "failed"
    assert outcomes["chemistry-scheme"]["variants"]["degraded"]["overall"] == "failed"
    assert outcomes["physics-optics"]["variants"]["degraded"]["overall"] == "failed"
    assert outcomes["soft-illustration"]["variants"]["good"]["overall"] == "needs_review"
    assert "strong_structural" not in outcomes["soft-illustration"]["edge_classes"]


def test_missing_bound_object_is_a_failure_not_a_crash(tmp_path):
    from scientific_figure.builder import SceneBuilder
    from scientific_figure.constraints import check_constraints
    from scientific_figure.project import write_project

    module = _suite()
    case = module.CASES["chemistry-scheme"]
    root = tmp_path / "case"
    root.mkdir()
    reference = root / "reference.png"
    case["reference"]().save(reference)
    request = root / "request.json"
    request.write_text('{"schema_version": "0.1", "background": "#ffffff"}', encoding="utf-8")
    pack = module.build_constraints(reference, request, root / "pack")
    builder = SceneBuilder(module.WIDTH, module.HEIGHT, background="#ffffff", schema_version="0.3")
    bindings = case["draw"](builder, "degraded")
    for binding in bindings:
        if binding["kind"] == "repetition":
            binding["group"] = binding.get("group") or pack["repetitions"][0]["id"]
    write_project(builder.build(), root / "project", scale=1)
    report = check_constraints(root / "project", root / "pack", bindings=bindings)
    missing = [entry for entry in report["bindings"] if entry["status"] == "failed"]
    assert missing, report["bindings"]
    assert any("missing from the project" in note for entry in missing for note in entry["notes"])

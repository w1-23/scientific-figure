"""Runtime preflight: local probes, honest status, and the receipt wiring."""
import json

from PIL import Image, ImageDraw
import pytest

from scientific_figure.preflight import preflight_status, run_preflight
from scientific_figure.preparation import prepare
from scientific_figure.prepared import consume


def answering(detail):
    return lambda: detail


def exploding(message):
    def probe():
        raise RuntimeError(message)
    return probe


def test_ok_when_every_probe_answers():
    report = run_preflight([("renderer", True, answering({"bytes": 92})),
                            ("pathops", False, answering({"version": "0.9.2"}))])
    assert report["status"] == "ok"
    assert [check["id"] for check in report["checks"]] == ["renderer", "pathops"]
    assert all(check["status"] == "ok" for check in report["checks"])
    assert report["adapter_status"] == "adapter_not_available"
    assert report["host_call_order_verified"] is False
    json.dumps(report, allow_nan=False)


def test_a_required_probe_failure_fails_the_run():
    report = run_preflight([("renderer", True, exploding("resvg missing")),
                            ("pathops", False, answering({"version": "0.9.2"}))])
    assert report["status"] == "failed"
    renderer = report["checks"][0]
    assert renderer["required"] is True
    assert renderer["status"] == "unavailable"
    assert "resvg missing" in renderer["detail"]


def test_an_optional_probe_failure_only_degrades():
    report = run_preflight([("renderer", True, answering({"bytes": 92})),
                            ("pathops", False, exploding("no module named pathops"))])
    assert report["status"] == "degraded"
    assert report["checks"][1]["status"] == "unavailable"


def test_a_failing_probe_does_not_raise_and_its_detail_is_bounded():
    report = run_preflight([("renderer", True, exploding("x" * 5000))])
    assert report["status"] == "failed"
    assert len(report["checks"][0]["detail"]) <= 300


def test_the_real_preflight_covers_this_host():
    report = run_preflight()
    checks = {check["id"]: check for check in report["checks"]}
    assert set(checks) == {"renderer", "fonts", "schemas", "opencv", "pathops"}
    for identifier in ("renderer", "fonts", "schemas"):
        assert checks[identifier]["status"] == "ok", checks[identifier]
    assert report["status"] in {"ok", "degraded"}


def test_status_vocabulary_is_shared_and_closed():
    assert preflight_status({"status": "ok"}) == "ok"
    assert preflight_status("degraded") == "degraded"
    for bad in ({}, {"status": "integrated"}, {"status": "not_integrated"}, None):
        with pytest.raises(ValueError):
            preflight_status(bad)


def build(tmp_path):
    image = Image.new("RGB", (64, 48), "white")
    ImageDraw.Draw(image).rectangle((10, 10, 40, 30), fill="#204060")
    image.save(tmp_path / "input.png")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": "0.1", "task": "single_asset",
                                   "inputs": [{"id": "main", "path": "input.png"}]}),
                       encoding="utf-8")
    return prepare(request, tmp_path / "prepared"), tmp_path / "prepared"


def test_prepare_records_the_report_in_receipt_and_manifest(tmp_path):
    receipt, root = build(tmp_path)
    manifest = json.loads((root / "input-manifest.json").read_text(encoding="utf-8"))
    assert receipt["preflight_status"] == manifest["preflight_status"]
    assert receipt["preflight_status"] == manifest["preflight"]["status"]
    assert receipt["preflight"] == manifest["preflight"]
    assert {"status", "checks", "adapter_status",
            "host_call_order_verified"} <= set(receipt["preflight"])


def test_consume_returns_the_report_and_keeps_the_adapter_unavailable(tmp_path):
    receipt, root = build(tmp_path)
    result = consume(receipt)
    assert result["runtime_preflight"]["status"] in {"ok", "degraded"}
    assert result["runtime_preflight"]["adapter_status"] == "adapter_not_available"
    assert result["preparation_preflight"] == receipt["preflight"]


def test_consume_rejects_a_manifest_preflight_outside_the_vocabulary(tmp_path):
    receipt, root = build(tmp_path)
    path = root / "input-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["preflight"]["status"] = "integrated"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        consume(root)


def test_consume_rejects_a_manifest_whose_preflight_was_swapped(tmp_path):
    receipt, root = build(tmp_path)
    path = root / "input-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["preflight_status"] = manifest["preflight"]["status"] = "failed"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt"):
        consume(receipt)

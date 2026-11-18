"""Local runtime preflight: what this host can actually do right now.

The checks cover the parts of the runtime this tool itself uses: the SVG
renderer, font measurement, the bundled schemas, OpenCV and the optional
geometry backend. Results are recorded in preparation receipts and manifests,
so a later reader can tell a run that had every dependency from one that was
degraded.

It deliberately says nothing about a model runtime: nothing in this tool can
observe a host's first inference, so ``adapter_status`` stays
``not_available`` and ``host_call_order_verified`` stays false. A green
preflight means the local tooling ran its probes, not that a model was
reached, read the figure, or did so before the first request.
"""
from __future__ import annotations

import datetime
import json
from io import BytesIO

from .scene import schema_validate

SCHEMA_VERSION = "0.1"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _renderer():
    from PIL import Image
    from .render import render_svg
    data = render_svg('<svg xmlns="http://www.w3.org/2000/svg" width="12" height="8">'
                      '<rect width="12" height="8" fill="#336699" /></svg>', scale=1)
    with Image.open(BytesIO(data)) as image:
        if image.size != (12, 8):
            raise ValueError(f"renderer produced {image.size}, expected (12, 8)")
        if not image.convert("RGB").getbbox():
            raise ValueError("renderer produced a blank image")
    return {"bytes": len(data)}


def _fonts():
    from .text_tools import measure_text
    metrics = measure_text("Cell")
    if not metrics["width"] or metrics["width"] <= 0:
        raise ValueError("font measurement returned no width")
    return {"width": round(float(metrics["width"]), 3)}


def _schemas():
    schema_validate({"schema_version": "0.1", "revision": 0,
                     "canvas": {"width": 16, "height": 16}, "objects": []}, "scene")
    return {"scene_schema": "validated"}


def _opencv():
    import cv2
    return {"version": cv2.__version__}


def _pathops():
    from importlib import import_module
    import_module("pathops")
    from .pathops_backend import backend_info
    return {"version": backend_info()["version"]}


PROBES = (("renderer", True, _renderer), ("fonts", True, _fonts), ("schemas", True, _schemas),
          ("opencv", False, _opencv), ("pathops", False, _pathops))


def _check(identifier, required, probe):
    try:
        detail = probe()
        status = "ok"
    except Exception as error:  # a preflight reports failures; it does not raise
        detail = f"{type(error).__name__}: {error}"[:300]
        status = "unavailable"
    return {"id": identifier, "required": required, "status": status, "detail": detail}


def run_preflight(probes=None):
    """Probe the local runtime and return a JSON-serializable report."""
    checks = [_check(identifier, required, probe)
              for identifier, required, probe in (PROBES if probes is None else probes)]
    failed = [check["id"] for check in checks if check["required"] and check["status"] != "ok"]
    degraded = [check["id"] for check in checks if not check["required"] and check["status"] != "ok"]
    status = "failed" if failed else "degraded" if degraded else "ok"
    return {"schema_version": SCHEMA_VERSION, "kind": "runtime_preflight", "status": status,
            "checked_at": _now(), "checks": checks,
            "adapter_status": "adapter_not_available", "host_call_order_verified": False,
            "limitations": [
                "Probes the local renderer, fonts, schemas, OpenCV and optional pathops only.",
                "No model runtime is probed: adapter_status stays not_available.",
                "A green result does not prove the host called this tool before its first inference."]}


def preflight_status(report):
    """The status vocabulary shared by receipts and manifests."""
    status = report.get("status") if isinstance(report, dict) else report
    if status not in {"ok", "degraded", "failed"}:
        raise ValueError("unsupported preflight status: " + json.dumps(status))
    return status

"""Evidence check for the plant-cell reconstruction (special regression sample).

The redraw was produced earlier without a constraint pack. This script imports
the delivered SVG as a Scene, builds a multi-scale edge pack from the reference,
and asks the pack one question per organelle: does the drawn object still cover
the reference's boundaries in its own region?

It reports; it does not redraw. Failures are the interesting output.

    python examples/plant-nitrogen-assimilation/check.py work/plantcheck --deliver examples/plant-nitrogen-assimilation
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from scientific_figure.constraints import build_constraints, check_constraints
from scientific_figure.geometry import Geometry
from scientific_figure.project import write_project
from scientific_figure.svg_import import import_svg

HERE = Path(__file__).resolve().parent
TARGETS = [
    ("cell-envelope", "outline", "boundary", 0.6, 3.0),
    ("vacuole", "outline", "boundary", 0.6, 4.0),
    ("chloroplast-top-left", "object", "boundary", 0.6, 3.0),
    ("chloroplast-top-right", "object", "boundary", 0.6, 3.0),
    ("chloroplast-bottom-left", "object", "boundary", 0.6, 3.0),
    ("mitochondrion-left", "object", "boundary", 0.55, 3.0),
    ("mitochondrion-upper-right", "object", "boundary", 0.55, 3.0),
    ("mitochondrion-right", "object", "boundary", 0.55, 3.0),
    ("nucleus", "object", "boundary", 0.6, 3.0),
    ("rough-endoplasmic-reticulum", "object", "boundary", 0.45, 4.0),
    ("golgi", "object", "boundary", 0.45, 4.0),
]


def _region(bounds, canvas, pad=6):
    x0, y0, x1, y1 = bounds
    left = max(0, int(x0) - pad)
    top = max(0, int(y0) - pad)
    right = min(canvas[0], int(x1) + pad)
    bottom = min(canvas[1], int(y1) + pad)
    return [left, top, right - left, bottom - top]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work", type=Path)
    parser.add_argument("--deliver", type=Path)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    reference = args.work / "reference.png"
    from PIL import Image
    with Image.open(HERE / "reference.webp") as image:
        image.convert("RGB").save(reference)

    scene = import_svg(HERE / "redraw.svg", schema_version="0.3")
    project = args.work / "project"
    if project.exists():
        shutil.rmtree(project)
    write_project(scene, project, scale=1)
    canvas = (int(scene["canvas"]["width"]), int(scene["canvas"]["height"]))
    geometry = Geometry(scene)

    request = args.work / "constraints-request.json"
    request.write_text(json.dumps({"schema_version": "0.1", "background": "#ffffff",
                                   "scales": ["coarse", "medium", "fine"]}), encoding="utf-8")
    pack_dir = args.work / "constraints"
    if not pack_dir.exists():
        build_constraints(reference, request, pack_dir)

    bindings, missing = [], []
    for name, scope, kind, recall, deviation in TARGETS:
        if name not in geometry.nodes or geometry.visible_bounds(name) is None:
            missing.append(name)
            continue
        bindings.append({"object_id": name, "kind": kind, "scope": scope,
                         "source_region": _region(geometry.visible_bounds(name), canvas),
                         "allowed_deviation": deviation, "recall_min": recall})
    report = check_constraints(project, pack_dir, bindings=bindings,
                               output=args.work / "constraint-check.json")
    results = [{key: entry[key] for key in ("object_id", "kind", "status", "source_region")}
               | {"recall": entry["metrics"].get("recall"),
                  "mean_offset": entry["metrics"].get("mean_offset"),
                  "notes": entry["notes"]}
               for entry in report["bindings"]]
    summary = {"figure": "plant-nitrogen-assimilation",
               "reference": "reference.webp (1456 x 1141)",
               "drawing": "redraw.svg imported as Scene 0.3",
               "overall": report["overall"]["status"],
               "counts": report["summary"],
               "missing_objects": missing,
               "bindings": results,
               "limitations": report["limitations"]}
    (args.work / "evidence-check.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if args.deliver:
        (args.deliver / "evidence-check.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    for entry in results:
        print(f"{entry['object_id']:32s} {entry['status']:12s} recall={entry['recall']} offset={entry['mean_offset']}")
    return summary


if __name__ == "__main__":
    main()

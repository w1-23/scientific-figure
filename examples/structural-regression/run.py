"""Cross-domain structural regression samples for the evidence loop.

Four small figures from different fields, each written twice from the same
reference: a faithful drawing and a deliberately degraded one. The suite is a
regression check for the wiring, not a drawing benchmark:

* the reference pack must expose the structure the case is about;
* the faithful drawing must satisfy the bound evidence;
* the degraded drawing must be reported as a failure, so a lost internal
  structure cannot pass as success.

Every case is deterministic and CPU only. Run it with:

    python examples/structural-regression/run.py work/regression --deliver examples/structural-regression
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from scientific_figure.builder import SceneBuilder
from scientific_figure.constraints import build_constraints, check_constraints
from scientific_figure.project import write_project

WIDTH, HEIGHT = 420, 300
BACKGROUND = "#ffffff"


# --------------------------------------------------------------------------- #
# A. Materials science: stacked layers, pores, cracks.
# --------------------------------------------------------------------------- #
def reference_materials():
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 200, 379, 264), fill="#5b6b7a")
    draw.rectangle((40, 120, 379, 199), fill="#cfd8de")
    for x in (75, 130, 185, 240, 295, 350):
        draw.ellipse((x - 14, 146, x + 14, 174), fill="#ffffff")
    draw.rectangle((40, 80, 379, 119), fill="#d98c3f")
    draw.line((150, 80, 150, 119), fill="#7a4a1a", width=3)
    draw.line((270, 80, 270, 119), fill="#7a4a1a", width=3)
    return image


def draw_materials(builder, variant):
    builder.group("layers")
    builder.rect("substrate", 40, 200, 340, 65, parent="layers", style={"fill": "#5b6b7a"})
    builder.group("porous-layer", parent="layers")
    builder.rect("interlayer", 40, 120, 340, 80, parent="porous-layer", style={"fill": "#cfd8de"})
    if variant == "good":
        for index, x in enumerate((75, 130, 185, 240, 295, 350), 1):
            builder.ellipse(f"pore-{index}", x, 160, 14, 14, parent="porous-layer",
                            style={"fill": "#ffffff"})
    else:
        builder.rect("pores", 75, 146, 275, 28, parent="porous-layer", style={"fill": "#ffffff"})
    builder.rect("coating", 40, 80, 340, 40, parent="layers", style={"fill": "#d98c3f"})
    for index, x in ((1, 150), (2, 270)):
        builder.path(f"crack-{index}", f"M{x} 80L{x} 119", parent="layers",
                     style={"fill": "none", "stroke": "#7a4a1a", "stroke_width": 3})
    return [{"object_id": "substrate", "kind": "boundary",
             "source_region": [30, 190, 360, 85], "allowed_deviation": 3.0, "recall_min": 0.6},
            {"object_id": "layers", "kind": "repetition",
             "source_region": [60, 140, 300, 60], "min_count": 5,
             "allowed_deviation": 3.0, "recall_min": 0.6}]


# --------------------------------------------------------------------------- #
# B. Chemistry: three rings, bonds, a reaction arrow, a solvent cluster.
# --------------------------------------------------------------------------- #
def _hexagon(draw, cx, cy, radius, color="#2f4858", width=4):
    points = [(cx + radius * math.cos(math.radians(60 * i)),
               cy + radius * math.sin(math.radians(60 * i))) for i in range(6)]
    draw.line(points + [points[0]], fill=color, width=width, joint="curve")


def reference_chemistry():
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    for cx in (100, 210, 320):
        _hexagon(draw, cx, 150, 34)
    draw.line((134, 150, 176, 150), fill="#2f4858", width=3)
    draw.line((244, 150, 286, 150), fill="#2f4858", width=3)
    draw.line((100, 240, 320, 240), fill="#333333", width=3)
    draw.polygon([(320, 240), (308, 233), (308, 247)], fill="#333333")
    draw.polygon([(196, 214), (210, 214), (203, 228)], fill="#2f4858")
    for index in range(8):
        x = 54 + index * 40
        draw.ellipse((x - 4, 272, x + 4, 280), fill="#7a8b99")
    return image


def _ring_path(cx, cy, radius):
    points = [(round(cx + radius * math.cos(math.radians(60 * i)), 2),
               round(cy + radius * math.sin(math.radians(60 * i)), 2)) for i in range(6)]
    head = f"M{points[0][0]} {points[0][1]}"
    body = "".join(f"L{x} {y}" for x, y in points[1:])
    return head + body + "Z"


def draw_chemistry(builder, variant):
    builder.group("scheme")
    centers = (100, 210, 320) if variant == "good" else (100, 320)
    for index, cx in enumerate(centers, 1):
        builder.path(f"ring-{index}", _ring_path(cx, 150, 34), parent="scheme",
                     style={"fill": "none", "stroke": "#2f4858", "stroke_width": 4})
    if variant == "good":
        builder.group("bonds", parent="scheme")
        builder.path("bond-1", "M134 150L176 150", parent="bonds",
                     style={"fill": "none", "stroke": "#2f4858", "stroke_width": 3})
        builder.path("bond-2", "M244 150L286 150", parent="bonds",
                     style={"fill": "none", "stroke": "#2f4858", "stroke_width": 3})
    builder.group("arrow", parent="scheme")
    builder.path("reaction", "M100 240L320 240", parent="arrow",
                 style={"fill": "none", "stroke": "#333333", "stroke_width": 3})
    builder.path("arrow-head", "M320 240L308 233L308 247Z", parent="arrow",
                 style={"fill": "#333333"})
    builder.path("catalyst", "M196 214L210 214L203 228Z", parent="arrow",
                 style={"fill": "#2f4858"})
    builder.group("solvent", parent="scheme")
    for index in range(8 if variant == "good" else 3):
        x = 54 + index * 40
        builder.ellipse(f"solvent-{index + 1}", x, 276, 4, 4, parent="solvent",
                        style={"fill": "#7a8b99"})
    return [{"object_id": "ring-1", "kind": "boundary",
             "source_region": [60, 110, 80, 80], "allowed_deviation": 3.0, "recall_min": 0.6},
            {"object_id": "ring-3", "kind": "boundary",
             "source_region": [280, 110, 80, 80], "allowed_deviation": 3.0, "recall_min": 0.6},
            {"object_id": "bonds", "kind": "boundary",
             "source_region": [130, 140, 160, 20], "allowed_deviation": 3.0, "recall_min": 0.5}]


# --------------------------------------------------------------------------- #
# C. Physics: laser, converging beam, lens, screen with a focus spot.
# --------------------------------------------------------------------------- #
def reference_optics():
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 130, 110, 170), fill="#37474f")
    draw.line((110, 150, 250, 150), fill="#d33b2f", width=4)
    draw.polygon([(250, 138), (350, 150), (250, 162)], fill="#f0a49c")
    draw.ellipse((242, 108, 258, 192), outline="#4c9ac2", width=4)
    draw.rectangle((352, 95, 366, 205), fill="#eceff1", outline="#37474f", width=2)
    draw.ellipse((352, 143, 366, 157), fill="#d33b2f")
    return image


def draw_optics(builder, variant):
    builder.group("bench")
    builder.rect("laser", 40, 130, 70, 40, parent="bench", style={"fill": "#37474f"})
    if variant == "good":
        builder.path("beam", "M110 150L250 150", parent="bench",
                     style={"fill": "none", "stroke": "#d33b2f", "stroke_width": 4})
        builder.path("converging", "M250 138L350 150L250 162Z", parent="bench",
                     style={"fill": "#f0a49c"})
        builder.ellipse("focus", 359, 150, 7, 7, parent="bench", style={"fill": "#d33b2f"})
    else:
        builder.path("beam", "M110 150L350 150", parent="bench",
                     style={"fill": "none", "stroke": "#d33b2f", "stroke_width": 4})
    builder.ellipse("lens", 250, 150, 8, 42, parent="bench",
                    style={"fill": "none", "stroke": "#4c9ac2", "stroke_width": 4})
    builder.rect("screen", 352, 95, 14, 110, parent="bench",
                 style={"fill": "#eceff1", "stroke": "#37474f", "stroke_width": 2})
    return [{"object_id": "laser", "kind": "boundary",
             "source_region": [30, 120, 90, 60], "allowed_deviation": 3.0, "recall_min": 0.6},
            {"object_id": "lens", "kind": "boundary",
             "source_region": [232, 98, 36, 104], "allowed_deviation": 3.0, "recall_min": 0.6},
            {"object_id": "converging", "kind": "boundary",
             "source_region": [245, 130, 110, 40], "allowed_deviation": 4.0, "recall_min": 0.5},
            {"object_id": "screen", "kind": "boundary",
             "source_region": [345, 88, 30, 124], "allowed_deviation": 3.0, "recall_min": 0.6}]


# --------------------------------------------------------------------------- #
# D. Soft illustration: a gradient blob with a diffuse edge and no outline.
# --------------------------------------------------------------------------- #
def reference_soft():
    ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH]
    t = np.clip(xs / WIDTH * 0.6 + ys / HEIGHT * 0.4, 0, 1)
    start, end = np.array([236, 246, 240], float), np.array([47, 107, 84], float)
    gradient = start + (end - start) * t[..., None]
    inside = ((xs - 210) / 120.0) ** 2 + ((ys - 150) / 80.0) ** 2 <= 1
    alpha = np.asarray(Image.fromarray((inside * 255).astype(np.uint8))
                       .filter(ImageFilter.GaussianBlur(4)), float) / 255
    image = 255 * (1 - alpha[..., None]) + gradient * alpha[..., None]
    return Image.fromarray(np.rint(image).astype(np.uint8))


def draw_soft(builder, variant):
    if variant == "good":
        builder.resource("blob-shade", "linear_gradient", start=[0.25, 0.1], end=[0.85, 1.0],
                         stops=[[0, "#e8f3ec"], [1, "#3f7f63"]])
        builder.ellipse("blob", 210, 150, 120, 80, style={"fill": "url(#blob-shade)"})
    else:
        builder.ellipse("blob", 210, 150, 120, 80,
                        style={"fill": "#a9c9b6", "stroke": "#2f4858", "stroke_width": 3})
    return [{"object_id": "blob", "kind": "boundary",
             "source_region": [70, 50, 280, 200], "allowed_deviation": 7.0, "recall_min": 0.4}]


CASES = {
    "materials-layered": {"reference": reference_materials, "draw": draw_materials,
                          "expect": {"good": "passed", "degraded": "failed"}},
    "chemistry-scheme": {"reference": reference_chemistry, "draw": draw_chemistry,
                         "expect": {"good": "passed", "degraded": "failed"}},
    "physics-optics": {"reference": reference_optics, "draw": draw_optics,
                       "expect": {"good": "passed", "degraded": "failed"}},
    "soft-illustration": {"reference": reference_soft, "draw": draw_soft,
                          "variants": ("good",), "forbid_class": "strong_structural",
                          "expect": {"good": "needs_review"}},
}


def _repetition_group(pack, region):
    x, y, w, h = region
    for group in pack["repetitions"]:
        x0, y0, x1, y1 = group["bbox"]
        if x0 <= x + w and x1 >= x and y0 <= y + h and y1 >= y:
            return group["id"]
    return None


def _write_project(builder, directory):
    write_project(builder.build(), directory, scale=2)
    return directory


def run_case(name, case, work, deliver):
    root = work / name
    root.mkdir(parents=True, exist_ok=True)
    reference = root / "reference.png"
    case["reference"]().save(reference)
    if deliver:
        case["reference"]().save(deliver / f"reference-{name}.png")
    request = root / "constraints-request.json"
    request.write_text(json.dumps({"schema_version": "0.1", "background": BACKGROUND,
                                   "scales": ["coarse", "medium", "fine"]}), encoding="utf-8")
    pack_dir = root / "constraints"
    if not pack_dir.exists():
        build_constraints(reference, request, pack_dir)
    pack = json.loads((pack_dir / "constraints.json").read_text(encoding="utf-8"))

    classes = sorted({component["class"] for scale in pack["edge_packs"].values()
                      for component in scale["components"]})
    record = {"case": name, "reference": f"reference-{name}.png",
              "edge_classes": classes, "variants": {}}
    for variant in case.get("variants", ("good", "degraded")):
        builder = SceneBuilder(WIDTH, HEIGHT, background=BACKGROUND, schema_version="0.3")
        bindings = case["draw"](builder, variant)
        for binding in bindings:
            if binding["kind"] == "repetition" and not binding.get("group"):
                binding["group"] = _repetition_group(pack, binding["source_region"])
        project = _write_project(builder, root / variant)
        (root / f"bindings-{variant}.json").write_text(json.dumps(bindings, indent=2), encoding="utf-8")
        report = check_constraints(project, pack_dir, bindings=bindings,
                                   output=root / f"check-{variant}.json")
        statuses = {entry["object_id"]: entry["status"] for entry in report["bindings"]}
        record["variants"][variant] = {
            "overall": report["overall"]["status"],
            "recall": report["overall"]["recall"],
            "precision": report["overall"]["precision"],
            "bindings": statuses,
            "summary": report["summary"],
            "expect": case["expect"][variant],
            "ok": report["overall"]["status"] == case["expect"][variant]}
        if deliver and variant == "good":
            from scientific_figure.svg import to_svg
            svg = to_svg(builder.build())
            (deliver / f"redraw-{name}.svg").write_text(svg, encoding="utf-8")
            (deliver / f"redraw-{name}.png").write_bytes((project / "preview.png").read_bytes())
    forbidden = case.get("forbid_class")
    record["ok"] = all(variant["ok"] for variant in record["variants"].values()) and (
        forbidden is None or forbidden not in classes)
    return record


def _markdown(summary):
    lines = ["# Structural regression samples", "",
             "Generated by `run.py` from each case's own reference and constraint pack. "
             "Statuses are `constraints check` results, not drawing-quality scores.", "",
             "| Case | Variant | Overall | Bound objects |", "|---|---|---|---|"]
    for record in summary["cases"]:
        for variant, entry in record["variants"].items():
            bound = ", ".join(f"{key}: {value}" for key, value in entry["bindings"].items())
            lines.append(f"| {record['case']} | {variant} | {entry['overall']} | {bound} |")
    lines += ["", "| Case | Edge classes in the pack |", "|---|---|"]
    for record in summary["cases"]:
        lines.append(f"| {record['case']} | {', '.join(record['edge_classes'])} |")
    lines += ["",
              "## What each case asserts", "",
              "- **materials-layered** — a stacked coating with six pores: the repetition "
              "binding must still count five or more separate openings, so merging them "
              "into one band fails.",
              "- **chemistry-scheme** — three rings and their bonds: deleting a ring and the "
              "bonds fails the bound objects instead of passing silently.",
              "- **physics-optics** — laser, converging beam, lens and screen: drawing the "
              "beam straight through the lens fails the `converging` binding.",
              "- **soft-illustration** — a gradient blob with a diffuse edge: the pack must "
              "hold no `strong_structural` class, and the blurry boundary stays "
              "`needs_review` (no edge evidence) rather than being forced into hard paths.",
              "",
              "## Limits", "",
              "- These are synthetic, small references. They verify the evidence wiring and "
              "detection, not visual quality or scientific correctness.",
              "- The negative-space heuristic still reports `needs_review` on stacked layers; "
              "the materials case therefore binds repetition and boundary, not negative space.",
              "- `constraints check` compares edges and coarse region signatures only; it does "
              "not verify colour gradients, labels or meaning."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work", type=Path)
    parser.add_argument("--deliver", type=Path)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    if args.deliver:
        args.deliver.mkdir(parents=True, exist_ok=True)
    records = [run_case(name, case, args.work, args.deliver) for name, case in CASES.items()]
    summary = {"cases": records, "render": "svg via engine, PNG from the same source",
               "scope": "detection wiring; not a drawing-quality benchmark"}
    (args.work / "regression.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if args.deliver:
        (args.deliver / "regression.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        (args.deliver / "REPORT.md").write_text(_markdown(summary), encoding="utf-8")
    for record in records:
        print(record["case"], {k: v["overall"] for k, v in record["variants"].items()})
    return summary


if __name__ == "__main__":
    main()

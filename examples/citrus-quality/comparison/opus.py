"""Render an original, editable citrus workflow using the checkout engine."""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import random

from scientific_figure.builder import SceneBuilder
from scientific_figure.project import write_project

HERE = Path(__file__).resolve().parent

PAPER = "#fbf8ef"
WHITE = "#fffdf7"
INK = "#243e32"
MUTED = "#4f6254"
SAGE = "#d9e1d2"
GREEN = "#446851"
TEAL = "#2e6966"
MINT = "#e7f1eb"
OCHRE = "#9d541f"
CREAM = "#f7ebd6"
LIGHT_BLUE = "#e4eeef"


class Drawing:
    def __init__(self):
        self.objects = []
        self.resources = []

    def node(self, key, kind, geometry=None, *, parent=None, style=None, transform=None):
        record = {"id": key, "kind": kind, "visible": True}
        for name, value in (("geometry", geometry), ("parent", parent),
                            ("style", style), ("transform", transform)):
            if value is not None:
                record[name] = value
        self.objects.append(record)
        return key

    def group(self, key, x=0, y=0, *, parent=None, zoom=1, angle=0):
        a = math.radians(angle)
        c, s = zoom * math.cos(a), zoom * math.sin(a)
        return self.node(key, "group", parent=parent, transform=[c, s, -s, c, x, y])

    def path(self, parent, key, d, fill="none", stroke="none", width=1, **style):
        self.node(f"{parent}-{key}", "path", {"d": d}, parent=parent,
                  style={"fill": fill, "stroke": stroke, "stroke_width": width, **style})

    def box(self, parent, key, x, y, w, h, fill, stroke="none", width=1):
        self.node(f"{parent}-{key}", "rect", {"x": x, "y": y, "width": w, "height": h},
                  parent=parent, style={"fill": fill, "stroke": stroke, "stroke_width": width})

    def oval(self, parent, key, x, y, rx, ry, fill, stroke="none", width=1):
        self.node(f"{parent}-{key}", "ellipse", {"cx": x, "cy": y, "rx": rx, "ry": ry},
                  parent=parent, style={"fill": fill, "stroke": stroke, "stroke_width": width})

    def text(self, parent, key, x, y, text, size, color=INK, anchor="start"):
        self.node(f"{parent}-{key}", "text", {"x": x, "y": y, "text": text}, parent=parent,
                  style={"fill": color, "font_size": size, "anchor": anchor})

    def arrow(self, parent, key, d, color=GREEN, width=1.65):
        self.path(parent, key, d, stroke=color, width=width, arrow_end=True)

    def gradient(self, key, kind, stops, **geometry):
        self.resources.append({"id": key, "kind": kind, "stops": stops, **geometry})


def fruit(d, parent, key, x, y, zoom, maturity, angle=0):
    p = d.group(f"{parent}-{key}", x, y, parent=parent, zoom=zoom, angle=angle)
    borders = {"green": "#577041", "turning": "#8f6e2d", "orange": "#9e4e23"}
    d.path(p, "rind", "M -3 -35 C 9 -39 28 -31 35 -17 C 42 -2 39 21 25 32 "
           "C 13 42 -11 41 -25 31 C -38 21 -42 3 -36 -12 C -31 -28 -15 -37 -3 -35 Z",
           f"url(#rind-{maturity})", borders[maturity], 1.25)
    rng = random.Random(161)
    for i in range(51):
        theta = rng.uniform(0, math.tau)
        r = math.sqrt(rng.random()) * 32
        xx, yy = r * math.cos(theta), 2 + r * math.sin(theta)
        radius = rng.uniform(0.35, 0.86)
        d.oval(p, f"pore-{i}", round(xx, 2), round(yy, 2), radius, radius * 0.78,
               borders[maturity] if i % 3 else "#ffe9bb")
    d.path(p, "highlight", "M -27 -11 C -24 -20 -19 -24 -12 -27", stroke="#fff4ce",
           width=1.5, stroke_opacity=0.65)
    d.path(p, "calyx", "M -10 -34 L -4 -38 L -2 -44 L 2 -38 L 9 -35 L 3 -34 L 0 -30 Z",
           "#465c35", "#425431", 0.7)


def leaf(d, parent, key, x, y, zoom, angle, dark=False):
    p = d.group(f"{parent}-{key}", x, y, parent=parent, zoom=zoom, angle=angle)
    d.path(p, "blade", "M 0 0 C 12 -19 35 -24 60 -7 C 47 -4 31 18 0 0 Z",
           "url(#leaf-shade)" if dark else "url(#leaf-light)", "#4e6948", 1)
    d.path(p, "midrib", "M -5 1 Q 24 -7 57 -7", stroke="#c8d1a2", width=0.85)
    for i, (start, y0, end) in enumerate([(10, -2, 19), (20, -4, 31), (32, -6, 43)]):
        d.path(p, f"veins-{i}", f"M {end} -13 Q {start + 3} -8 {start} {y0} "
               f"Q {start + 5} 0 {end + 2} 4", stroke="#d3d9b5", width=0.56)


def tag(d, p, key, x, y, value, tint=WHITE):
    d.box(p, f"{key}-tag", x, y, 50, 25, tint, "#b7c5b1")
    d.text(p, f"{key}-id", x + 25, y + 17, value, 13, anchor="middle")


def orchard(d, panel):
    p = d.group(f"{panel}-branch", 27, 47, parent=panel, zoom=0.84)
    d.path(p, "wood", "M 20 179 C 66 155 117 140 167 131 C 219 123 271 93 307 83",
           stroke="#79533b", width=8)
    d.path(p, "wood-light", "M 21 177 C 67 153 118 140 168 129 C 221 121 267 93 305 82",
           stroke="#bc9a72", width=2)
    d.path(p, "twigs", "M 68 157 Q 55 141 53 118 M 119 142 Q 126 160 123 174 "
           "M 157 133 Q 153 110 162 99 M 208 119 Q 218 132 229 143 M 259 102 Q 267 78 282 63 "
           "M 80 153 Q 82 179 75 195 M 166 131 Q 169 168 169 189 M 251 105 Q 248 129 257 158",
           stroke="#79533b", width=2.6)
    leaves = [(53, 118, 1.13, -126), (104, 148, 1.24, -104), (162, 99, 1.03, -71),
              (168, 130, 1.15, -37), (229, 143, 1.22, 23), (282, 63, 0.82, -22),
              (272, 98, 0.92, 5), (123, 174, 0.95, 155), (64, 159, 0.74, 166)]
    for i, (x, y, zoom, angle) in enumerate(leaves):
        leaf(d, p, f"leaf-{i}", x, y, zoom, angle, i % 3 == 0)
    for key, x, y, maturity, tilt in [("01", 74, 226, "green", -8),
                                       ("02", 167, 227, "turning", 5),
                                       ("03", 260, 199, "orange", 8)]:
        fruit(d, p, f"branch-{key}", x, y, 0.97, maturity, tilt)
        d.path(p, f"leader-{key}", f"M {x} {y + 38} v 10", stroke=GREEN)
        tag(d, p, f"branch-{key}", x - 25, y + 48, f"C{key}")
    p = panel
    d.text(p, "maturity", 166, 324, "Fruit at different maturity stages", 15, MUTED, "middle")
    d.arrow(p, "harvest", "M 166 341 v 23")
    d.oval(p, "crate-shadow", 166, 509, 118, 8, "#e8e4d5")
    d.path(p, "crate-back", "M 58 416 L 78 393 H 251 L 275 416 L 260 505 H 73 Z",
           "#ad8154", "#886340", 1.3)
    d.path(p, "crate-inside", "M 75 415 L 87 401 H 245 L 258 415 Z", "#806143")
    for key, x, maturity in [("01", 112, "green"), ("02", 165, "turning"), ("03", 218, "orange")]:
        fruit(d, p, f"crate-{key}", x, 416, 0.67, maturity)
    d.path(p, "crate-front", "M 60 424 H 273 L 259 504 H 74 Z", "#d9b37a", "#805c3e", 1.4)
    for i, y in enumerate([446, 477, 498]):
        d.path(p, f"slat-{i}", f"M 74 {y} H 261", stroke="#a27b55", width=1.3)
    for i, (x, end) in enumerate([(62, 76), (271, 257)]):
        d.path(p, f"brace-{i}", f"M {x} 425 L {end} 503", stroke="#9f7652", width=4)
    for i, key in enumerate(["01", "02", "03"]):
        tag(d, p, f"crate-{key}", 87 + 53 * i, 451, f"C{key}")
    d.text(p, "crate-caption", 166, 546, "Labeled orchard sample", 17, INK, "middle")


def probe(d, p, key, x, y, zoom=1, maturity="orange"):
    g = d.group(f"{p}-{key}", x, y, parent=p, zoom=zoom)
    d.oval(g, "shadow", 0, 49, 66, 8, "#e8e4d5")
    d.path(g, "cradle", "M -62 35 H 55 L 44 49 H -50 Z", "#d0d8ca", "#8c9d8d")
    fruit(d, g, "whole", -10, 0, 1.14, maturity, -8)
    d.path(g, "light", "M 22 -26 L 44 -58 L 56 -47 L 30 -16 Z", "#9acbc5", fill_opacity=0.73)
    d.path(g, "beam", "M 47 -52 L 27 -20", stroke=TEAL, width=1.25)
    d.path(g, "cable", "M 76 -95 C 100 -130 129 -101 130 -124", stroke=INK, width=3)
    d.path(g, "housing", "M 39 -59 L 65 -104 Q 68 -110 76 -106 L 90 -98 Q 96 -94 92 -87 "
           "L 64 -45 Z", "#426c64", "#274b44", 1.35)
    d.path(g, "housing-light", "M 48 -63 L 72 -101 L 78 -98 L 54 -59 Z", "#9dbdb0")
    d.path(g, "tip", "M 39 -59 L 64 -45 L 61 -38 L 35 -52 Z", "#b6c9be", "#274b44")


def scanning(d, p):
    probe(d, p, "probe", 121, 234, 1.14, maturity="turning")
    d.text(p, "probe-label", 17, 116, "Optical probe", 16, TEAL)
    d.path(p, "probe-leader", "M 123 111 H 170 L 194 132", stroke=TEAL, width=1.1)
    tag(d, p, "example", 18, 302, "C02")
    d.text(p, "intact-label", 80, 321, "Scan each intact fruit", 16)
    d.arrow(p, "save", "M 166 341 v 23")
    d.box(p, "spectra-card", 27, 383, 278, 146, WHITE, "#a7baaa")
    d.box(p, "spectra-band", 27, 383, 278, 42, MINT, "#a7baaa")
    d.text(p, "spectra-label", 41, 410, "Saved spectral records", 18)
    for i, key in enumerate(["C01", "C02", "C03"]):
        y = 448 + i * 31
        if i:
            d.path(p, f"spectra-rule-{i}", f"M 41 {y - 20} H 290", stroke=SAGE)
        d.text(p, f"spectra-id-{i}", 46, y, key, 14, TEAL)
        d.text(p, f"spectra-data-{i}", 111, y, "Spectrum", 15)
    d.text(p, "id-label", 166, 555, "Each record keeps its fruit ID", 15, MUTED, "middle")


def assays(d, p):
    d.text(p, "select-a", 166, 108, "Select fruit from the same batch", 16, anchor="middle")
    d.text(p, "select-b", 166, 129, "after scanning", 16, anchor="middle")
    fruit(d, p, "sample", 166, 175, 0.67, "turning", 6)
    tag(d, p, "sample", 141, 210, "C02")
    d.path(p, "branches", "M 166 243 V 257 H 78 M 166 257 H 252", stroke=GREEN, width=1.5)
    d.arrow(p, "to-solids", "M 78 257 v 17")
    d.arrow(p, "to-acidity", "M 252 257 v 17")
    # Refractometer pictogram and titration apparatus.
    d.oval(p, "refract-shadow", 79, 354, 52, 5, "#e8e4d5")
    d.path(p, "refractometer", "M 36 325 L 108 291 L 129 310 L 55 345 Z",
           "#5c8179", "#3f6056", 1.5)
    d.path(p, "refractometer-bands", "M 43 322 L 63 337 M 106 293 L 124 312", stroke="#d2e0d7", width=4)
    d.path(p, "refractometer-window", "M 87 308 L 104 300 L 112 308 L 95 316 Z", CREAM, "#486558")
    d.oval(p, "flask-shadow", 251, 354, 52, 5, "#e8e4d5")
    d.path(p, "flask", "M 244 313 H 258 M 246 313 V 327 L 233 347 Q 231 351 236 351 H 266 "
           "Q 271 351 269 347 L 256 327 V 313", "#eef5ec", TEAL, 1.6)
    d.path(p, "indicator", "M 238 340 Q 244 337 250 340 Q 258 342 264 339 L 269 347 "
           "Q 270 350 265 350 H 237 Q 233 350 235 346 Z", "#e5b3a4")
    d.path(p, "burette", "M 248 281 H 254 V 301 L 251 307 L 248 301 Z", LIGHT_BLUE, TEAL, 1.2)
    d.path(p, "burette-tap", "M 243 299 H 259 M 257 296 V 302", stroke=TEAL, width=1.3)
    d.text(p, "solids", 78, 379, "Soluble solids", 16, anchor="middle")
    d.text(p, "acidity", 252, 379, "Titratable acidity", 16, anchor="middle")
    d.arrow(p, "record-assays", "M 166 393 v 17")
    d.box(p, "match-card", 27, 426, 278, 103, WHITE, "#a7baaa")
    d.text(p, "match-title", 166, 452, "Match records by fruit ID", 18, anchor="middle")
    tag(d, p, "spectral-key", 46, 465, "C02", MINT)
    tag(d, p, "assay-key", 236, 465, "C02", CREAM)
    d.path(p, "matched-pair", "M 98 478 H 233", stroke=TEAL, width=1.8)
    d.box(p, "link-label-bg", 151, 468, 31, 20, WHITE)
    d.text(p, "link-label", 166, 483, "=", 17, TEAL, "middle")
    d.text(p, "spectral-side", 71, 514, "Spectrum", 13, MUTED, "middle")
    d.text(p, "assay-side", 261, 514, "Assays", 13, MUTED, "middle")
    d.text(p, "matched-label", 166, 555, "Paired spectral and reference data", 15, MUTED, "middle")


def modeling(d, p):
    d.text(p, "split-label", 166, 109, "Split paired data by fruit", 17, anchor="middle")
    d.path(p, "split-stem", "M 166 121 V 132 H 87 M 166 132 H 245", stroke=GREEN, width=1.5)
    d.arrow(p, "to-calibration", "M 87 132 v 11")
    d.arrow(p, "to-validation", "M 245 132 v 11")
    for key, x, title, subtitle, fill in [
        ("calibration", 15, "Calibration set", "Fit model", MINT),
        ("validation", 173, "Independent", "validation set", LIGHT_BLUE),
    ]:
        d.box(p, f"{key}-card", x, 153, 144, 59, fill, "#a7baaa")
        d.text(p, key, x + 72, 177, title, 16, anchor="middle")
        d.text(p, f"{key}-action", x + 72, 197, subtitle, 14, MUTED, "middle")
    d.arrow(p, "calibrate-model", "M 87 212 V 271 H 169")
    d.arrow(p, "validate-model", "M 245 247 V 218", TEAL)
    d.text(p, "test-model", 255, 234, "Test", 13, TEAL)
    d.box(p, "model-card", 179, 247, 138, 62, GREEN)
    d.text(p, "model", 248, 272, "Quality model", 17, WHITE, "middle")
    d.text(p, "model-targets", 248, 293, "Solids and acidity", 13, WHITE, "middle")
    d.path(p, "prediction-divider", "M 15 332 H 317", stroke="#c5d0c2")
    probe(d, p, "new-probe", 81, 412, 0.48)
    d.text(p, "new-scan", 81, 462, "New fruit scans", 15, TEAL, "middle")
    d.arrow(p, "apply", "M 142 401 H 169")
    d.arrow(p, "model-to-prediction", "M 248 309 V 370")
    d.box(p, "predict-card", 179, 380, 138, 43, CREAM, "#b9a17b")
    d.text(p, "predict", 248, 407, "Predict quality", 16, OCHRE, "middle")
    d.path(p, "outputs-stem", "M 248 423 V 484 H 87", stroke=GREEN, width=1.5)
    d.arrow(p, "to-individual", "M 87 484 V 495")
    d.arrow(p, "to-batch", "M 248 484 V 495")
    for key, x, title, subtitle in [
        ("individual", 15, "Individual fruit", "Prediction per ID"),
        ("batch", 176, "Batch assessment", "Summarize predictions"),
    ]:
        d.box(p, f"{key}-output", x, 506, 144, 56, WHITE, "#a7baaa")
        d.text(p, key, x + 72, 529, title, 15, anchor="middle")
        d.text(p, f"{key}-detail", x + 72, 549, subtitle, 12, MUTED, "middle")


def make_scene():
    d = Drawing()
    rind_palettes = {
        "green": [(0, "#d6d99c"), (0.66, "#95ae66"), (1, "#69874a")],
        "turning": [(0, "#f5dd8d"), (0.56, "#dab353"), (1, "#9e943e")],
        "orange": [(0, "#ffe198"), (0.58, "#ecaa50"), (1, "#c97532")],
    }
    for key, stops in rind_palettes.items():
        d.gradient(f"rind-{key}", "radial_gradient", stops, center=[0.35, 0.33], radius=0.76)
    d.gradient("leaf-light", "linear_gradient", [(0, "#9aad72"), (1, "#628455")],
               start=[0.2, 0], end=[0.65, 1])
    d.gradient("leaf-shade", "linear_gradient", [(0, "#7d9966"), (1, "#4d704b")],
               start=[0.2, 0], end=[0.65, 1])
    h = d.group("heading")
    d.text(h, "title", 47, 68, "Non-destructive prediction of citrus quality", 42)
    d.path(h, "title-rule", "M 48 97 H 1532", stroke="#c5d0bf", width=1.2)
    for i, (x, key, title, paint, draw) in enumerate([
        (46, "orchard", "Orchard sampling", GREEN, orchard),
        (431, "scan", "Intact-fruit scanning", TEAL, scanning),
        (816, "reference", "Reference assays", OCHRE, assays),
        (1201, "modeling", "Modeling & prediction", GREEN, modeling),
    ], 1):
        p = d.group(key, x, 118)
        d.box(p, "panel", 0, 0, 332, 583, WHITE, SAGE)
        d.box(p, "rule", 0, 0, 332, 3, paint)
        d.text(p, "step", 17, 46, f"0{i}", 16, paint)
        d.text(p, "title", 53, 47, title, 23)
        d.path(p, "header-rule", "M 16 69 H 316", stroke=SAGE)
        draw(d, p)
    timeline = d.group("workflow-arrows")
    for i, x in enumerate([384, 769, 1154], 1):
        d.arrow(timeline, f"next-{i}", f"M {x} 154 h 33", width=2.2)
    builder = SceneBuilder(1580, 737, background=PAPER, schema_version="0.3")
    builder.add_many(d.objects, resources=d.resources)
    return builder.build()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", default="figure", help="New project directory name beside this script")
    args = parser.parse_args()
    if Path(args.project_name).name != args.project_name or args.project_name in {"", ".", ".."}:
        parser.error("use a directory name without path components")
    print(write_project(make_scene(), HERE / args.project_name, scale=2))


if __name__ == "__main__":
    main()

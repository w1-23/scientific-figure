"""Render the citrus-quality workflow described in the repository README."""
from argparse import ArgumentParser
from math import cos, hypot, pi, sin
from pathlib import Path
import shutil

from scientific_figure.builder import SceneBuilder
from scientific_figure.project import write_project


WIDTH, HEIGHT = 1320, 822
PAPER = "#fffefa"
WHITE = "#ffffff"
INK = "#20322d"
MUTED = "#4e625a"
SAGE = "#326447"
GREEN = "#235943"
BLUE = "#285e7d"
AMBER = "#89521f"
RULE = "#d7e0d5"


class Figure:
    def __init__(self):
        self.objects = []
        self.resources = []

    def add(self, oid, kind, geometry=None, parent=None, transform=None, **style):
        obj = {"id": oid, "kind": kind}
        if geometry is not None:
            obj["geometry"] = geometry
        if parent is not None:
            obj["parent"] = parent
        if transform is not None:
            obj["transform"] = transform
        if style:
            obj["style"] = style
        self.objects.append(obj)
        return oid

    def group(self, oid, parent=None, transform=None):
        return self.add(oid, "group", parent=parent, transform=transform)

    def path(self, oid, shape, fill="none", *, stroke=None, width=1, parent=None):
        style = {"fill": fill}
        if stroke is not None:
            style.update(stroke=stroke, stroke_width=width)
        return self.add(oid, "path", {"d": shape}, parent, **style)

    def rect(self, oid, x, y, w, h, fill, *, radius=0, stroke=None, parent=None):
        style = {"fill": fill}
        if stroke is not None:
            style.update(stroke=stroke, stroke_width=1.2)
        return self.add(oid, "rect", {"x": x, "y": y, "width": w, "height": h, "rx": radius},
                        parent, **style)

    def ellipse(self, oid, x, y, rx, ry, fill, *, parent=None, opacity=1):
        return self.add(oid, "ellipse", {"cx": x, "cy": y, "rx": rx, "ry": ry},
                        parent, fill=fill, opacity=opacity)

    def text(self, oid, x, y, label, size, *, paint=INK, center=False, bold=False, parent=None):
        style = {"fill": paint, "font_size": size, "anchor": "middle" if center else "start"}
        if bold:
            style.update(stroke=paint, stroke_width=size * .036)
        return self.add(oid, "text", {"x": x, "y": y, "text": label}, parent, **style)

    def line(self, oid, x1, y1, x2, y2, paint, width=1.5, parent=None):
        return self.path(oid, f"M {x1} {y1} L {x2} {y2}", stroke=paint, width=width, parent=parent)

    def gradient(self, oid, colors):
        stops = [[i / (len(colors) - 1), color] for i, color in enumerate(colors)]
        self.resources.append({"id": oid, "kind": "linear_gradient", "start": [.14, .04],
                               "end": [.85, 1], "stops": stops})
        return f"url(#{oid})"

    def arrow(self, oid, *points, paint, parent=None):
        group = self.group(oid, parent)
        (x0, y0), (x, y) = points[-2:]
        distance = hypot(x - x0, y - y0)
        ux, uy = (x - x0) / distance, (y - y0) / distance
        bx, by = x - ux * 8, y - uy * 8
        shaft = (*points[:-1], (bx + ux, by + uy))
        self.path(f"{oid}-shaft", "M " + " L ".join(f"{a:.2f} {b:.2f}" for a, b in shaft),
                  stroke=paint, width=2, parent=group)
        self.path(f"{oid}-tip", f"M {x} {y} L {bx-uy*4:.2f} {by+ux*4:.2f} "
                  f"L {bx+uy*4:.2f} {by-ux*4:.2f} Z", paint, parent=group)

    def scene(self):
        builder = SceneBuilder(WIDTH, HEIGHT, background=PAPER, schema_version="0.3")
        builder.add_many(self.objects, resources=self.resources)
        return builder.build()


def panel(d, oid, letter, title, x, y, w, h, accent, tint):
    g = d.group(oid)
    d.rect(f"{oid}-panel", x, y, w, h, tint, radius=15, parent=g)
    d.ellipse(f"{oid}-letter-bg", x + 34, y + 34, 14, 14, accent, parent=g)
    d.text(f"{oid}-letter", x + 34, y + 39, letter, 14, paint=WHITE, center=True, bold=True, parent=g)
    d.text(f"{oid}-title", x + 58, y + 41, title, 22, bold=True, parent=g)
    return g


def leaf(d, oid, x, y, size, degrees, fill, parent):
    angle = degrees * pi / 180
    a, b = size * cos(angle), size * sin(angle)
    g = d.group(oid, parent, [a, b, -b, a, x, y])
    d.path(f"{oid}-blade", "M 0 0 C .28 -.32 .70 -.24 1 -.02 C .67 .25 .27 .24 0 0 Z", fill, parent=g)
    d.path(f"{oid}-midrib", "M .05 0 Q .44 .035 .93 -.02", stroke="#c7dda0", width=.012, parent=g)
    for i, t in enumerate((.30, .52, .73), 1):
        d.path(f"{oid}-veins-{i}", f"M {t+.065} .009 l -.115 -.125 M {t+.03} .01 l -.08 .111",
               stroke="#a8c783", width=.007, parent=g)


def fruit(d, oid, x, y, radius, fill, parent):
    g = d.group(oid, parent, [radius, 0, 0, radius, x, y])
    d.path(f"{oid}-skin", "M -.15 -.88 C -.65 -1.07 -.99 -.54 -1 -.05 "
           "C -1.01 .59 -.63 .94 -.05 .96 C .58 1.00 1 .58 1 .00 "
           "C 1 -.58 .63 -1.02 .18 -.9 Q 0 -.84 -.15 -.88 Z", fill, parent=g)
    d.path(f"{oid}-stem", "M .02 -.86 Q -.03 -1.03 .07 -1.17", stroke="#725035", width=.055, parent=g)
    d.path(f"{oid}-calyx", "M -.20 -.89 Q -.08 -.99 .02 -.91 Q .12 -.98 .23 -.91 "
           "Q .09 -.80 .01 -.87 Q -.07 -.80 -.20 -.89 Z", SAGE, parent=g)
    d.ellipse(f"{oid}-light", -.34, -.44, .24, .13, "#fff4c7", parent=g, opacity=.48)
    pores = ((-.53, -.19), (-.68, .02), (-.45, .11), (-.25, -.21), (-.05, -.34),
             (.35, -.52), (.52, -.33), (.65, -.15), (.49, .09), (.27, .30),
             (.61, .35), (.43, .53), (.18, .63), (-.15, .47), (-.43, .35))
    for i, (px, py) in enumerate(pores, 1):
        d.ellipse(f"{oid}-pore-{i}", px, py, .026, .019, "#fff4c7", parent=g, opacity=.48)
    return g


def orchard(d, paints):
    g = panel(d, "orchard", "A", "Orchard sampling", 48, 128, 272, 646, SAGE, "#edf3e8")
    d.text("orchard-caption", 70, 199, "Sample across maturity stages", 15, paint=MUTED, parent=g)

    branch = d.group("fruiting-branch", g)
    d.path("branch-main", "M 69 418 Q 137 346 195 297 Q 235 263 292 231", stroke="#806143", width=7, parent=branch)
    d.path("branch-shoots", "M 106 379 Q 114 347 112 316 M 161 325 Q 186 318 204 338 "
           "M 212 284 Q 218 252 208 231 M 252 254 Q 271 268 292 265 "
           "M 240 267 Q 261 274 261 291", stroke="#94704c", width=3, parent=branch)
    for name, x, y, length, angle, fill in (
        ("left-upper", 112, 319, 66, -116, paints["leaf-light"]),
        ("left", 119, 363, 62, 200, paints["leaf"]),
        ("middle-upper", 158, 329, 80, -102, paints["leaf"]),
        ("upper", 210, 258, 52, -110, paints["leaf-light"]),
        ("centre", 177, 313, 80, -25, paints["leaf"]),
        ("right", 241, 265, 62, 22, paints["leaf-light"]),
        ("lower", 143, 343, 66, 86, paints["leaf"]),
        ("tip", 248, 249, 60, -32, paints["leaf"]),
    ):
        leaf(d, f"branch-leaf-{name}", x, y, length, angle, fill, branch)
    fruit(d, "branch-green-fruit", 111, 343, 25, paints["green"], branch)
    fruit(d, "branch-turning-fruit", 202, 376, 34, paints["turning"], branch)
    fruit(d, "branch-ripe-fruit", 258, 337, 40, paints["orange"], branch)

    for i, (name, x, fill) in enumerate((("Green", 92, paints["green"]),
                                        ("Turning", 176, paints["turning"]),
                                        ("Ripe", 260, paints["orange"])), 1):
        d.ellipse(f"maturity-swatch-{i}", x, 457, 7, 7, fill, parent=g)
        d.text(f"maturity-label-{i}", x, 482, name, 14, paint=MUTED, center=True, parent=g)
    d.arrow("harvest", (184, 500), (184, 529), paint=SAGE, parent=g)

    crate = d.group("sampling-crate", g)
    d.ellipse("crate-shadow", 182, 643, 103, 10, "#bbcbb4", parent=crate, opacity=.54)
    d.path("crate-back", "M 88 577 L 113 549 L 250 549 L 279 577 L 267 632 L 100 632 Z",
           "#a77443", parent=crate)
    fruit(d, "crate-green-fruit", 122, 569, 23, paints["green"], crate)
    fruit(d, "crate-turning-fruit", 170, 562, 28, paints["turning"], crate)
    fruit(d, "crate-ripe-fruit", 230, 565, 27, paints["orange"], crate)
    fruit(d, "crate-front-fruit", 199, 582, 24, paints["orange"], crate)
    d.path("crate-front", "M 88 578 L 279 578 L 267 639 Q 267 643 263 643 H 105 Q 101 643 100 637 Z",
           paints["wood"], parent=crate)
    d.line("crate-plank-gap", 98, 611, 270, 611, "#ad7545", 2, crate)
    d.line("crate-front-lip", 88, 578, 279, 578, "#edc895", 4, crate)
    d.rect("crate-handle", 163, 588, 42, 8, "#805733", radius=4, parent=crate)
    d.path("crate-tag-string", "M 243 576 Q 255 577 252 601", stroke="#765634", parent=crate)
    d.rect("crate-tag", 223, 600, 47, 26, "#fff7df", radius=4, parent=crate)
    d.text("crate-tag-id", 246.5, 618, "C-01", 13, paint=AMBER, center=True, bold=True, parent=crate)
    d.text("orchard-output", 184, 687, "Label each fruit", 18, center=True, bold=True, parent=g)
    d.text("orchard-output-note", 184, 713, "Keep its ID with every record", 14, paint=MUTED, center=True, parent=g)


def optical_scan(d, paints):
    g = panel(d, "optical-scan", "B", "Whole-fruit optical scan", 372, 128, 442, 282, BLUE, "#edf4f7")
    d.text("scan-caption", 394, 199, "Record the spectrum from the intact fruit", 15, paint=MUTED, parent=g)
    device = d.group("optical-station", g)
    d.ellipse("station-shadow", 501, 357, 104, 10, "#b5ccd6", parent=device, opacity=.57)
    d.path("station-frame", "M 423 337 V 260 Q 423 231 450 224 H 521 V 238 H 453 "
           "Q 437 238 437 260 V 337 Z", paints["housing"], parent=device)
    d.path("station-frame-light", "M 428 335 V 261 Q 428 235 451 230 H 518", stroke="#c4d9e1",
           width=2, parent=device)
    d.rect("station-probe", 488, 228, 51, 19, "#285b73", radius=5, parent=device)
    d.rect("station-probe-window", 499, 245, 29, 5, "#79b5c7", radius=2, parent=device)
    d.path("incident-light", "M 501 250 H 526 L 538 289 H 486 Z", "#c1e4eb", parent=device)
    d.line("incident-ray", 509, 252, 499, 277, "#397f98", 1.6, device)
    d.line("return-ray", 523, 277, 518, 253, "#397f98", 1.6, device)
    d.ellipse("sample-seat", 512, 330, 51, 12, "#7396a6", parent=device)
    d.ellipse("sample-seat-top", 512, 327, 44, 8, "#aec6d1", parent=device)
    fruit(d, "scanned-fruit", 512, 300, 30, paints["orange"], device)
    d.rect("station-base", 405, 336, 185, 18, paints["housing"], radius=6, parent=device)
    d.rect("station-base-light", 414, 340, 158, 2, "#acc9d5", radius=1, parent=device)
    d.ellipse("station-led", 577, 345, 3, 3, "#aaf3d1", parent=device)
    d.arrow("station-to-record", (597, 285), (621, 285), paint=BLUE, parent=g)

    record = d.group("spectrum-record", g)
    d.rect("spectrum-record-sheet", 632, 233, 160, 116, WHITE, radius=9, stroke="#c0d4df", parent=record)
    d.text("spectrum-record-title", 646, 256, "Spectrum record", 15, bold=True, parent=record)
    d.line("spectrum-record-rule", 646, 266, 777, 266, "#d4e1e8", parent=record)
    for i, (label, y) in enumerate((("Fruit ID", 289), ("Scan", 320)), 1):
        d.text(f"spectrum-field-{i}", 647, y, label, 13, paint=MUTED, parent=record)
    d.rect("spectrum-id-chip", 715, 274, 61, 21, "#e4eff4", radius=5, parent=record)
    d.text("spectrum-id", 745.5, 289, "C-01", 13, paint=BLUE, center=True, bold=True, parent=record)
    for i in range(5):
        d.rect(f"spectrum-channel-{i+1}", 716 + i * 12, 309, 8, 12, "#bdd8e4", radius=2, parent=record)
    d.text("optical-station-label", 499, 383, "Optical probe", 14, paint=MUTED, center=True, parent=g)


def citrus_slice(d, parent, x, y):
    g = d.group("citrus-slice", parent, [1, 0, 0, .69, x, y])
    d.ellipse("slice-peel-side", 0, 7, 39, 37, "#d88627", parent=g)
    d.ellipse("slice-peel", 0, 0, 39, 38, "#f5b248", parent=g)
    d.ellipse("slice-pith", 0, 0, 35.5, 34.5, "#fff0c4", parent=g)
    for i in range(8):
        a, b = i * pi / 4 + .045, (i + 1) * pi / 4 - .045
        ax, ay, bx, by = (round(v * 32, 2) for v in (cos(a), sin(a), cos(b), sin(b)))
        d.path(f"slice-segment-{i+1}", f"M 0 0 L {ax} {ay} A 32 32 0 0 1 {bx} {by} Z",
               "#f5a24d" if i % 2 else "#f8b566", parent=g)
        for j, r in enumerate((15, 24), 1):
            d.ellipse(f"slice-vesicle-{i+1}-{j}", r * cos((a + b) / 2), r * sin((a + b) / 2),
                      2.8, 1.5, "#ffcc87", parent=g)
    d.ellipse("slice-core", 0, 0, 5, 4.5, "#fff0c4", parent=g)


def lab(d):
    g = panel(d, "lab", "C", "Reference quality assays", 372, 448, 442, 326, AMBER, "#faf0e2")
    d.text("lab-caption", 394, 519, "Assay selected fruit after scanning", 15, paint=MUTED, parent=g)
    d.line("lab-bench", 395, 659, 791, 659, "#dbc6a7", 2, g)

    slice_group = d.group("sample-dish", g)
    d.ellipse("dish-shadow", 459, 649, 53, 9, "#dfccb2", parent=slice_group)
    d.ellipse("dish-rim", 459, 642, 47, 11, "#e2e9e7", parent=slice_group)
    d.ellipse("dish-inner", 459, 641, 40, 7, WHITE, parent=slice_group)
    citrus_slice(d, slice_group, 459, 622)

    meter = d.group("refractometer", g)
    d.path("refractometer-side", "M 525 605 L 624 605 L 613 649 H 525 Z", "#78949e", parent=meter)
    d.path("refractometer-top", "M 525 605 L 546 574 H 623 Q 629 575 628 581 L 624 605 Z",
           "#b7cdd3", parent=meter)
    d.path("refractometer-screen", "M 563 582 H 595 L 592 594 H 553 Z", "#37647a", parent=meter)
    d.ellipse("refractometer-sample-well", 613, 585, 8, 5, "#769eac", parent=meter)
    d.path("refractometer-droplet", "M 613 569 Q 625 583 613 586 Q 602 584 613 569 Z", "#e9a346", parent=meter)
    d.rect("refractometer-button", 540, 616, 19, 10, "#dae7e9", radius=4, parent=meter)

    titration = d.group("titration", g)
    d.rect("titration-stand-base", 679, 644, 97, 9, "#8ca1a4", radius=3, parent=titration)
    d.line("titration-stand-pole", 749, 544, 749, 644, "#789094", 4, titration)
    d.line("titration-clamp", 720, 563, 749, 563, "#789094", 4, titration)
    d.rect("burette-tube", 714, 538, 12, 49, "#e3eef0", radius=3, stroke="#8aa8ad", parent=titration)
    d.rect("burette-fluid", 716, 553, 8, 31, "#b4d3da", radius=2, parent=titration)
    for i in range(4):
        d.line(f"burette-mark-{i+1}", 715, 547 + 8*i, 720, 547 + 8*i, "#6f9298", 1, titration)
    d.line("burette-tip", 720, 587, 720, 601, "#75999e", 2, titration)
    d.line("burette-stopcock", 712, 588, 728, 588, BLUE, 3, titration)
    d.path("titration-flask", "M 712 604 H 728 V 615 L 743 638 Q 746 645 739 649 "
           "H 701 Q 694 645 697 638 L 712 615 Z", "#e7f1ef", stroke="#87a6a5", width=1.3, parent=titration)
    d.path("titration-liquid", "M 705 633 H 735 L 740 641 Q 742 646 737 646 H 703 "
           "Q 698 646 700 641 Z", "#f5c477", parent=titration)

    for name, x, label in (("slice", 459, "Sample prep"), ("refractometer", 578, "Soluble solids"),
                           ("titration", 721, "Titratable acidity")):
        d.text(f"lab-label-{name}", x, 685, label, 14, paint=MUTED, center=True, parent=g)
    d.rect("assay-output-box", 394, 709, 398, 42, WHITE, radius=8, stroke="#e2ccad", parent=g)
    d.text("assay-output-label", 409, 736, "Reference measurements", 15, bold=True, parent=g)
    d.text("assay-output-id", 746, 736, "C-01", 14, paint=AMBER, center=True, bold=True, parent=g)


def modelling(d, paints):
    g = panel(d, "modelling", "D", "Model and predict", 880, 128, 392, 646, GREEN, "#edf3ef")
    d.text("pairing-title", 902, 206, "Match records by fruit ID", 17, bold=True, parent=g)
    paired = d.group("paired-records", g)
    for name, y, label, color, tint in (("spectra", 220, "Spectrum record", BLUE, "#e6f0f6"),
                                        ("assays", 270, "Reference measurements", AMBER, "#f9eddc")):
        d.rect(f"paired-{name}", 902, y, 348, 42, WHITE, radius=8, stroke=RULE, parent=paired)
        d.rect(f"paired-{name}-tag", 914, y + 10, 57, 22, tint, radius=5, parent=paired)
        d.text(f"paired-{name}-id", 942.5, y + 26, "C-01", 13, paint=color, center=True, bold=True, parent=paired)
        d.text(f"paired-{name}-label", 984, y + 27, label, 15, parent=paired)
    d.line("paired-id-link", 942.5, 262, 942.5, 270, GREEN, 1.5, paired)

    d.text("split-caption", 1076, 348, "Split by fruit", 14, paint=MUTED, center=True, parent=g)
    d.line("split-stem", 1076, 313, 1076, 329, GREEN, 2, g)
    d.line("split-branch", 991, 364, 1165, 364, GREEN, 2, g)
    d.arrow("split-calibration", (991, 364), (991, 385), paint=GREEN, parent=g)
    d.arrow("split-validation", (1165, 364), (1165, 385), paint=GREEN, parent=g)
    for name, x, title, line1, line2 in (
        ("calibrate", 902, "Fit model", "Calibration fruit", "Spectra + assays"),
        ("validate", 1088, "Validate model", "Held-out fruit", "Prediction vs. assay"),
    ):
        d.rect(f"{name}-box", x, 393, 162, 88, WHITE, radius=9, stroke=RULE, parent=g)
        d.text(f"{name}-title", x+81, 422, title, 16, center=True, bold=True, parent=g)
        d.text(f"{name}-input", x+81, 448, line1, 14, paint=MUTED, center=True, parent=g)
        d.text(f"{name}-comparison", x+81, 468, line2, 13, paint=MUTED, center=True, parent=g)
    d.arrow("model-to-validation", (1065, 432), (1086, 432), paint=GREEN, parent=g)
    d.arrow("validation-to-deployment", (1169, 482), (1169, 509), paint=GREEN, parent=g)

    d.rect("prediction-banner", 902, 516, 348, 50, GREEN, radius=9, parent=g)
    d.text("prediction-title", 1076, 548, "Predict quality from new scans", 17, paint=WHITE,
           center=True, bold=True, parent=g)
    d.line("output-split-stem", 1076, 566, 1076, 580, GREEN, 2, g)
    d.line("output-split-branch", 983, 580, 1169, 580, GREEN, 2, g)
    for name, x, label, note in (("individual", 902, "Individual fruit", "Predicted traits"),
                                  ("batch", 1088, "Batch assessment", "Quality summary")):
        center = x + 81
        d.arrow(f"prediction-to-{name}", (center, 580), (center, 603), paint=GREEN, parent=g)
        d.rect(f"{name}-box", x, 610, 162, 142, WHITE, radius=9, stroke=RULE, parent=g)
        d.text(f"{name}-title", center, 710, label, 15, center=True, bold=True, parent=g)
        d.text(f"{name}-note", center, 733, note, 14, paint=MUTED, center=True, parent=g)
    fruit(d, "prediction-fruit", 969, 662, 22, paints["orange"], g)
    d.rect("prediction-report", 989, 637, 32, 44, "#e3eee8", radius=4, stroke="#b9cfc1", parent=g)
    for i in range(3):
        y = 649 + i * 9
        d.ellipse(f"prediction-report-bullet-{i+1}", 997, y, 1.7, 1.7, GREEN, parent=g)
        d.line(f"prediction-report-line-{i+1}", 1002, y, 1013, y, "#638a76", 1.2, g)
    for i, (x, y, fill) in enumerate(((1147, 655, paints["turning"]), (1190, 654, paints["orange"]),
                                      (1168, 666, paints["orange"])), 1):
        fruit(d, f"batch-fruit-{i}", x, y, 15, fill, g)
    d.path("batch-tray", "M 1128 673 H 1209 L 1202 686 H 1135 Z", "#c8ded2", parent=g)


def assemble():
    d = Figure()
    paints = {
        "green": d.gradient("green-peel", ("#c3d785", "#779745")),
        "turning": d.gradient("turning-peel", ("#e9d374", "#d8942c")),
        "orange": d.gradient("orange-peel", ("#ffc163", "#f19535", "#c6611c")),
        "leaf": d.gradient("leaf-dark", ("#77a364", "#2b6142")),
        "leaf-light": d.gradient("leaf-light", ("#99bc73", "#467749")),
        "wood": d.gradient("crate-wood", ("#dda86b", "#be8550")),
        "housing": d.gradient("optical-housing", ("#8faeba", "#41667a")),
    }
    title = d.group("heading")
    d.text("figure-title", 48, 66, "Non-destructive assessment of citrus fruit quality", 38, bold=True, parent=title)
    d.line("title-rule", 48, 96, 1272, 96, RULE, 1.3, title)
    orchard(d, paints)
    optical_scan(d, paints)
    lab(d)
    modelling(d, paints)

    flow = d.group("inter-panel-flow")
    d.arrow("sampling-to-scan", (320, 621), (345, 621), (345, 285), (369, 285), paint=SAGE, parent=flow)
    d.arrow("scan-to-assay", (590, 411), (590, 445), paint=AMBER, parent=flow)
    d.arrow("spectra-to-pairing", (794, 241), (898, 241), paint=BLUE, parent=flow)
    d.arrow("assays-to-pairing", (793, 729), (849, 729), (849, 291), (898, 291), paint=AMBER, parent=flow)
    return d.scene()


def deliver(project, out, scene):
    """Copy the figure files and write an inventory of the editable hierarchy."""
    out.mkdir(parents=True, exist_ok=True)
    for source, name in (("figure.svg", "citrus-quality.svg"), ("preview.png", "citrus-quality.png"),
                         ("scene.json", "citrus-quality.scene.json")):
        shutil.copyfile(project / source, out / name)

    children = {}
    for obj in scene["objects"]:
        children.setdefault(obj.get("parent"), []).append(obj)
    lines = ["# Editable objects", "", "Object IDs follow the Scene hierarchy and match SVG element IDs.", ""]

    def emit(obj, depth):
        detail = obj["kind"]
        if detail == "text":
            detail = obj["geometry"]["text"]
        lines.append(f"{'  ' * depth}- `{obj['id']}` — {detail}")
        for child in children.get(obj["id"], []):
            emit(child, depth + 1)

    for root in children[None]:
        emit(root, 0)
    (out / "OBJECTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--scale", type=float, default=2)
    parser.add_argument("--deliver", type=Path, help="copy SVG, PNG, Scene and object inventory here")
    args = parser.parse_args()
    scene = assemble()
    write_project(scene, args.project, scale=args.scale)
    if args.deliver:
        deliver(args.project, args.deliver, scene)

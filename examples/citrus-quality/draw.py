"""Draw an original citrus non-destructive quality-assessment schematic.

No reference image is used. The host designed the modules, geometry and text;
the engine only executes them into an editable Scene. Values are deliberately
qualitative: no R2, RMSE, wavelength or model-performance numbers are invented.

Run:  python draw.py OUTPUT_PROJECT
"""
import json
import shutil
from argparse import ArgumentParser
from functools import lru_cache
from math import cos, hypot, pi, radians, sin
from pathlib import Path

from PIL import ImageFont
from scientific_figure.builder import SceneBuilder
from scientific_figure.fonts import font_info
from scientific_figure.project import write_project

WIDTH, HEIGHT = 1400, 1040

INK = "#22313a"
MUTED = "#5d707b"
RULE = "#d5e0e4"
LEAF_DARK = "#2c6e47"
LEAF = "#4f9a5b"
LEAF_LIGHT = "#8cc07a"
FRUIT_GREEN = "#b8c94b"
FRUIT_TURN = "#dfb13d"
FRUIT_RIPE = "#ec8a2f"
PEEL = "#f4c66b"
ALBEDO = "#fbe7bf"
FLESH = "#f6a75a"
FLESH_DARK = "#ef9440"
INSTRUMENT = "#30556b"
INSTRUMENT_LIGHT = "#6f93a7"
SIGNAL = "#2b9fc0"
LAB = "#eef2ee"
BENCH = "#cfd8d2"
DATA = "#2c6f9c"
TEAL = "#2a9d8f"
PANEL = "#f5f9fa"


@lru_cache(maxsize=64)
def font(size):
    return ImageFont.truetype(font_info()["path"], round(size * 16))


class Figure:
    def __init__(self):
        self.objects = []
        self.resources = []
        self._serial = 0

    # -- primitives -------------------------------------------------------
    def _add(self, oid, kind, geometry, parent, transform=None, **style):
        obj = {"id": oid, "kind": kind}
        if geometry is not None:
            obj["geometry"] = geometry
        if parent:
            obj["parent"] = parent
        if transform:
            obj["transform"] = transform
        style = {name: value for name, value in style.items() if value is not None}
        if style:
            obj["style"] = style
        self.objects.append(obj)

    def group(self, oid, parent=None, transform=None):
        self._add(oid, "group", None, parent, transform)
        return oid

    def path(self, oid, d, fill="none", stroke=None, width=1, parent=None, opacity=None):
        self._add(oid, "path", {"d": d}, parent, fill=fill, stroke=stroke,
                  stroke_width=width if stroke else None, opacity=opacity)

    def rect(self, oid, x, y, w, h, fill, stroke=None, width=1, r=0, parent=None, opacity=None):
        geometry = {"x": x, "y": y, "width": w, "height": h}
        if r:
            geometry["rx"] = r
        self._add(oid, "rect", geometry, parent, fill=fill, stroke=stroke,
                  stroke_width=width if stroke else None, opacity=opacity)

    def ellipse(self, oid, cx, cy, rx, ry, fill, stroke=None, width=1, parent=None,
                opacity=None):
        self._add(oid, "ellipse", {"cx": cx, "cy": cy, "rx": rx, "ry": ry}, parent,
                  fill=fill, stroke=stroke, stroke_width=width if stroke else None,
                  opacity=opacity)

    def line(self, oid, x1, y1, x2, y2, stroke=INK, width=1.4, parent=None, opacity=None):
        self.path(oid, f"M {x1:.2f} {y1:.2f} L {x2:.2f} {y2:.2f}", stroke=stroke,
                  width=width, parent=parent, opacity=opacity)

    def text(self, oid, x, y, value, size, fill=INK, anchor="start", parent=None,
             bold=False, angle=0, limit=None):
        measured = font(size).getlength(value) / 16
        stretch = min(1, limit / measured) if limit else 1
        transform = None
        if stretch != 1 or angle:
            a = radians(angle)
            xx, xy, yx, yy = stretch * cos(a), stretch * sin(a), -sin(a), cos(a)
            transform = [xx, xy, yx, yy, x - xx * x - yx * y, y - xy * x - yy * y]
        self._add(oid, "text", {"x": x, "y": y, "text": value}, parent, transform,
                  fill=fill, font_size=size, anchor=anchor,
                  stroke=fill if bold else None, stroke_width=max(0.6, size * 0.032) if bold else None)

    def wrap(self, oid, x, y, value, size, width, *, leading=None, fill=INK, parent=None,
             bold=False, anchor="start"):
        leading = leading or size * 1.32
        words, line, lines = value.split(), "", []
        for word in words:
            trial = (line + " " + word).strip()
            if font(size).getlength(trial) / 16 <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
        for index, text in enumerate(lines):
            self.text(f"{oid}-{index + 1}", x, y + index * leading, text, size,
                      fill=fill, parent=parent, bold=bold, anchor=anchor)
        return len(lines)

    def linear(self, oid, colors, start=(0, 0), end=(0, 1), stops=None):
        if stops is None:
            stops = [i / (len(colors) - 1) for i in range(len(colors))]
        self.resources.append({"id": oid, "kind": "linear_gradient", "start": list(start),
                               "end": list(end),
                               "stops": [list(pair) for pair in zip(stops, colors)]})
        return f"url(#{oid})"

    def arrow(self, oid, x1, y1, x2, y2, color=DATA, width=2.4, head=9, parent=None,
              dashed=False):
        length = hypot(x2 - x1, y2 - y1)
        if length < 1e-6:
            raise ValueError("arrow needs distinct endpoints")
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        bx, by = x2 - ux * head, y2 - uy * head
        half = head * 0.46
        self.group(oid, parent)
        self.line(f"{oid}-shaft", x1, y1, bx + ux, by + uy, color, width, oid)
        self.path(f"{oid}-tip", f"M {x2:.2f} {y2:.2f} L {bx - uy * half:.2f} {by + ux * half:.2f} "
                  f"L {bx + uy * half:.2f} {by - ux * half:.2f} Z", color, parent=oid)
        if dashed:
            # Dashes are drawn as an explicit overlay so the shaft stays a real path.
            self.path(f"{oid}-dash", f"M {x1:.2f} {y1:.2f} L {bx:.2f} {by:.2f}", "none",
                      "#ffffff", width * 0.9, oid)

    # -- composition ------------------------------------------------------
    def scene(self):
        builder = SceneBuilder(WIDTH, HEIGHT, background="#ffffff", schema_version="0.3")
        builder.add_many(self.objects, resources=self.resources)
        return builder.build()


def polar(cx, cy, r, angle):
    return cx + r * cos(angle), cy + r * sin(angle)


def leaf(d, oid, cx, cy, length, angle, fill, parent, twist=0.18):
    a = radians(angle)
    dx, dy = cos(a), sin(a)
    nx, ny = -dy, dx
    tipx, tipy = cx + dx * length / 2, cy + dy * length / 2
    basex, basey = cx - dx * length / 2, cy - dy * length / 2
    width = length * 0.34
    d.path(oid, (
        f"M {basex:.2f} {basey:.2f} "
        f"C {cx - dx * length * .1 + nx * width:.2f} {cy - dy * length * .1 + ny * width:.2f} "
        f"{tipx - dx * length * .22 + nx * width * .7:.2f} {tipy - dy * length * .22 + ny * width * .7:.2f} "
        f"{tipx:.2f} {tipy:.2f} "
        f"C {tipx - dx * length * .22 - nx * width * .7:.2f} {tipy - dy * length * .22 - ny * width * .7:.2f} "
        f"{cx - dx * length * .1 - nx * width:.2f} {cy - dy * length * .1 - ny * width:.2f} "
        f"{basex:.2f} {basey:.2f} Z"), fill, parent=parent)
    d.path(f"{oid}-vein", f"M {basex:.2f} {basey:.2f} Q {cx + nx * twist * width:.2f} "
           f"{cy + ny * twist * width:.2f} {tipx:.2f} {tipy:.2f}", "none",
           "#ffffff", length * 0.012, parent)


def fruit(d, oid, cx, cy, r, paint, parent, stem=True, leaf_color=LEAF_DARK):
    d.group(oid, parent)
    d.ellipse(f"{oid}-body", cx, cy, r, r * 0.94, paint, parent=oid)
    d.path(f"{oid}-calyx", f"M {cx - r * .14:.2f} {cy - r * .88:.2f} "
           f"Q {cx:.2f} {cy - r * 1.02:.2f} {cx + r * .14:.2f} {cy - r * .88:.2f} Z",
           "#7b9a4a", parent=oid)
    d.ellipse(f"{oid}-shine", cx - r * .34, cy - r * .4, r * .26, r * .16,
              "#ffffff", parent=oid, opacity=.35)
    if stem:
        d.path(f"{oid}-stem", f"M {cx:.2f} {cy - r * .92:.2f} L {cx + r * .05:.2f} "
               f"{cy - r * 1.16:.2f}", "none", "#7a5a3a", r * .07, parent=oid)
        leaf(d, f"{oid}-leaf", cx + r * .22, cy - r * 1.08, r * .62, -34, leaf_color, oid)


def cut_fruit(d, oid, cx, cy, r, parent):
    d.group(oid, parent)
    d.ellipse(f"{oid}-peel", cx, cy, r, r * 0.96, PEEL, parent=oid)
    d.ellipse(f"{oid}-albedo", cx, cy, r * .9, r * .86, ALBEDO, parent=oid)
    d.ellipse(f"{oid}-flesh", cx, cy, r * .78, r * .74, FLESH, parent=oid)
    segments = 9
    for index in range(segments):
        a0 = -pi / 2 + 2 * pi * index / segments + 0.035
        a1 = -pi / 2 + 2 * pi * (index + 1) / segments - 0.035
        p0 = polar(cx, cy, r * .12, a0)
        p1 = polar(cx, cy, r * .72, a0)
        p2 = polar(cx, cy, r * .72, a1)
        p3 = polar(cx, cy, r * .12, a1)
        d.path(f"{oid}-segment-{index + 1}",
               f"M {p0[0]:.2f} {p0[1]:.2f} L {p1[0]:.2f} {p1[1]:.2f} "
               f"A {r * .72:.2f} {r * .72:.2f} 0 0 1 {p2[0]:.2f} {p2[1]:.2f} "
               f"L {p3[0]:.2f} {p3[1]:.2f} A {r * .12:.2f} {r * .12:.2f} 0 0 0 "
               f"{p0[0]:.2f} {p0[1]:.2f} Z",
               FLESH_DARK if index % 2 else FLESH,
               "#e59143", .7, oid, opacity=.92)
        # Juice vesicles: small editable marks inside each segment.
        for step in range(3):
            angle = a0 + (a1 - a0) * (0.25 + step * 0.25)
            radius = r * (0.34 + step * 0.12)
            px, py = polar(cx, cy, radius, angle)
            d.ellipse(f"{oid}-vesicle-{index + 1}-{step + 1}", px, py, r * .045,
                      r * .028, "#fbd7a4", parent=oid, opacity=.75)
    for index, angle in enumerate((-0.7, 0.6, 2.4)):
        sx, sy = polar(cx, cy, r * .38, angle)
        d.ellipse(f"{oid}-seed-{index + 1}", sx, sy, r * .055, r * .085, "#f7ecd4",
                  "#dcc79b", .6, oid)
    d.ellipse(f"{oid}-core", cx, cy, r * .11, r * .11, "#fdf3dd", parent=oid)
    d.path(f"{oid}-highlight", f"M {cx - r * .62:.2f} {cy - r * .5:.2f} "
           f"A {r * .8:.2f} {r * .8:.2f} 0 0 1 {cx - r * .2:.2f} {cy - r * .78:.2f}",
           "none", "#ffffff", r * .05, oid, opacity=.45)


def module_header(d, letter, title, x, y, color, parent=None):
    group = f"header-{letter}"
    d.group(group, parent)
    d.ellipse(f"{group}-chip", x + 13, y - 6, 13, 13, color, parent=group)
    d.text(f"{group}-letter", x + 13, y - 1.2, letter, 14, "#ffffff",
           anchor="middle", parent=group, bold=True)
    d.text(f"{group}-title", x + 36, y, title, 16.5, INK, parent=group, bold=True)


def draw_orchard(d):
    root = d.group("module-a")
    module_header(d, "A", "Orchard Sampling", 40, 146, LEAF, root)
    d.text("module-a-note", 40, 170, "Canopy fruit at three maturity stages", 11.5,
           MUTED, parent=root)

    tree = d.group("orchard-tree", root)
    trunk = d.linear("trunk-paint", ["#9a7047", "#7a5736"])
    d.path("tree-trunk", "M 176 566 C 180 520 178 470 181 420 C 183 396 184 380 186 362 "
           "L 208 362 C 208 382 208 398 209 420 C 211 470 210 520 214 566 Z",
           trunk, parent=tree)
    d.path("tree-branch-1", "M 186 430 C 160 410 140 392 120 372", "none", "#7a5736", 9, tree)
    d.path("tree-branch-2", "M 208 424 C 232 404 250 388 266 368", "none", "#7a5736", 9, tree)
    d.path("tree-branch-3", "M 196 400 C 196 378 196 360 197 344", "none", "#7a5736", 7, tree)

    canopy = d.linear("canopy-paint", ["#8cc47a", "#4f9a5b", "#2c6e47"],
                      start=(.1, .1), end=(.9, .95))
    d.path("tree-canopy", "M 190 210 C 246 206 286 246 288 296 C 290 342 262 372 224 384 "
           "C 214 414 176 420 156 396 C 112 402 78 372 74 330 C 70 286 100 250 140 240 "
           "C 152 220 168 210 190 210 Z", canopy, "#3d7d4c", 1.6, tree)
    d.path("tree-canopy-inner", "M 150 268 C 186 246 232 258 250 292 C 264 318 252 350 226 360 "
           "C 200 372 168 362 154 338 C 142 316 138 286 150 268 Z", "#5aa465",
           parent=tree, opacity=.85)
    leaf(d, "leaf-a1", 110, 300, 54, -40, LEAF_DARK, tree)
    leaf(d, "leaf-a2", 268, 296, 50, 38, LEAF_LIGHT, tree)
    leaf(d, "leaf-a3", 132, 372, 46, -6, LEAF, tree)
    leaf(d, "leaf-a4", 246, 366, 44, 22, LEAF_DARK, tree)

    fruit(d, "fruit-green", 128, 336, 29, FRUIT_GREEN, tree, leaf_color=LEAF_DARK)
    fruit(d, "fruit-turning", 256, 312, 30, FRUIT_TURN, tree, leaf_color=LEAF)
    fruit(d, "fruit-mature", 190, 408, 32, FRUIT_RIPE, tree, leaf_color=LEAF_DARK)

    for label, x, y, tx, ty in (("Green", 128, 336, 84, 300),
                                ("Turning", 256, 312, 286, 268),
                                ("Mature", 190, 408, 232, 448)):
        d.line(f"lead-{label.lower()}", x, y, tx, ty, MUTED, .9, root, opacity=.8)
        d.text(f"label-{label.lower()}", tx + (4 if tx > x else -4), ty - 4, label, 11.5,
               INK, anchor="start" if tx > x else "end", parent=root, bold=True)

    # Harvest tray and the harvested fruit requested by the brief.
    tray = d.group("harvest-tray", root)
    d.path("tray-body", "M 262 540 L 322 540 L 314 574 L 270 574 Z", "#c9a06a",
           "#a67f4e", 1.4, tray)
    d.path("tray-rim", "M 256 536 L 328 536", "none", "#8d6a40", 4, tray)
    d.ellipse("tray-fruit-1", 282, 534, 12, 11, FRUIT_TURN, parent=tray)
    d.ellipse("tray-fruit-2", 304, 530, 11, 10, FRUIT_RIPE, parent=tray)
    d.ellipse("tray-fruit-3", 296, 520, 10, 9, FRUIT_GREEN, parent=tray)
    d.arrow("harvest-arrow", 232, 466, 262, 512, LEAF_DARK, 2.2, 8, root)
    d.text("tray-label", 268, 596, "Sampled fruit", 11, MUTED, parent=root)
    d.arrow("subsample-arrow", 306, 606, 244, 690, LEAF_DARK, 1.5, 7, root)
    d.text("subsample-label", 286, 654, "Sub-sample", 9.4, LEAF_DARK, parent=root)

    bag = d.group("sampling-bag", root)
    d.path("bag-body", "M 128 704 L 244 704 L 234 828 L 138 828 Z", "#efe6cf",
           "#c9b98e", 1.4, bag)
    d.path("bag-gusset", "M 186 704 L 186 828", "none", "#d8c9a4", 1, bag)
    for index, (cx, cy, color) in enumerate(((158, 770, FRUIT_RIPE), (186, 786, FRUIT_TURN),
                                             (212, 768, FRUIT_GREEN))):
        d.ellipse(f"bag-fruit-{index + 1}", cx, cy, 14, 13, color, parent=bag)
    d.path("bag-top", "M 122 700 L 250 700", "none", "#a98f62", 5, bag)
    d.rect("bag-tag", 202, 726, 34, 20, "#ffffff", "#b9c9c4", 1, 4, bag)
    d.line("bag-tag-line-1", 208, 734, 230, 734, "#9fb2ac", 1, bag)
    d.line("bag-tag-line-2", 208, 740, 222, 740, "#9fb2ac", 1, bag)
    d.text("bag-label", 186, 856, "Labelled sample lot", 11, MUTED, anchor="middle",
           parent=root)
    d.text("bag-note", 186, 874, "cool chain, traceable ID", 9.6, MUTED, anchor="middle",
           parent=root)
    return root


def draw_detection(d):
    root = d.group("module-b")
    module_header(d, "B", "Non-destructive Measurement", 396, 146, INSTRUMENT, root)
    d.text("module-b-note", 396, 170, "Whole-fruit optics, no sample destruction", 11.5,
           MUTED, parent=root)

    fruit(d, "whole-fruit", 468, 330, 52, FRUIT_RIPE, root)
    d.arrow("fruit-to-cutaway", 526, 330, 584, 330, MUTED, 1.5, 7, root)
    d.text("whole-fruit-label", 468, 400, "Whole fruit", 11.5, MUTED, anchor="middle",
           parent=root)
    # The beam is painted first so tissue layers stay readable through it.
    beam = d.linear("beam-paint", [SIGNAL, "#bfe9f2"], start=(0, 0), end=(0, 1))
    d.path("optical-beam", "M 634 412 L 658 412 L 704 292 L 616 358 Z", beam,
           SIGNAL, .8, root, opacity=.24)
    d.path("optical-beam-core", "M 644 410 L 654 410 L 674 326 L 632 368 Z", SIGNAL,
           parent=root, opacity=.14)
    cut_fruit(d, "cutaway", 654, 326, 64, root)
    d.text("cutaway-label", 724, 280, "Peel · albedo", 11.5, MUTED, parent=root)
    d.text("cutaway-label-detail", 724, 298, "Segments · seeds", 10.2, MUTED,
           parent=root)
    d.text("signal-label", 724, 334, "Optical Signal", 11.5, DATA, parent=root, bold=True)
    d.line("signal-lead", 702, 330, 720, 330, DATA, .9, root, opacity=.7)

    device = d.group("device", root)
    body = d.linear("device-paint", ["#3d6479", "#27455a"], start=(0, 0), end=(0, 1))
    d.rect("device-body", 590, 486, 168, 92, body, "#1d3546", 1.2, 10, device)
    d.rect("device-screen", 604, 500, 78, 50, "#dcecef", "#1d3546", 1.0, 4, device)
    d.path("device-trace", "M 610 536 L 622 522 L 632 530 L 644 508 L 656 518 L 670 504",
           "none", SIGNAL, 1.8, device)
    d.ellipse("device-led", 742, 508, 4, 4, "#7fe0c8", parent=device)
    d.ellipse("device-knob", 742, 528, 7, 7, INSTRUMENT_LIGHT, parent=device)
    d.rect("device-vent", 700, 548, 42, 14, "#1d3546", r=3, parent=device, opacity=.6)
    d.text("device-label", 604, 570, "NIR probe", 10, "#cfe4ea", parent=device)

    d.path("probe-arm", "M 654 486 L 654 446 L 640 430", "none", "#b9c7ce", 7, device)
    d.rect("probe-head", 626, 414, 28, 18, "#cfdbe1", "#8ea3ae", 1, 3, device)

    card = d.group("spectral-card", root)
    d.rect("spectral-card-bg", 396, 470, 182, 118, "#ffffff", "#c9d8dd", 1.2, 8, card)
    d.text("spectral-title", 408, 492, "Spectral Acquisition", 11.5, INK, parent=card,
           bold=True)
    d.line("spectral-axis-x", 410, 566, 562, 566, "#9fb2bb", 1, card)
    d.line("spectral-axis-y", 410, 566, 410, 506, "#9fb2bb", 1, card)
    d.path("spectral-curve", "M 414 560 C 436 556 448 540 464 534 C 482 528 492 542 508 528 "
           "C 524 514 534 498 548 500 C 558 502 560 514 562 520", "none", SIGNAL, 2.2, card)
    d.path("spectral-curve-2", "M 414 562 C 438 560 450 552 466 548 C 484 544 494 552 510 544 "
           "C 526 536 538 526 552 528", "none", TEAL, 1.4, card, opacity=.7)
    return root


def draw_lab(d):
    root = d.group("module-c")
    module_header(d, "C", "Reference Quality Measurements", 396, 646, TEAL, root)
    d.text("module-c-note", 396, 670,
           "Destructive assays on representative sub-samples calibrate and validate the model",
           11.5, MUTED, parent=root)

    d.rect("bench-top", 380, 866, 430, 10, BENCH, r=2, parent=root)
    d.rect("bench-shadow", 396, 876, 400, 4, "#b9c4bd", r=2, parent=root, opacity=.5)

    # Digital balance.
    scale = d.group("balance", root)
    d.rect("balance-base", 412, 806, 92, 56, "#e7ecee", "#aab9c0", 1.2, 5, scale)
    d.rect("balance-pan", 426, 792, 64, 10, "#cfd9dd", "#9dafb7", 1, 2, scale)
    d.rect("balance-display", 434, 818, 48, 22, "#31474f", r=2, parent=scale)
    d.text("balance-readout", 458, 834, "— g", 10.5, "#8fe0b8", anchor="middle", parent=scale)
    d.rect("balance-sample", 442, 782, 32, 10, PEEL, r=4, parent=scale)

    # Refractometer with a fruit drop.
    refract = d.group("refractometer", root)
    d.path("refract-body", "M 516 842 L 560 842 L 576 806 L 530 806 Z", "#dfe8eb",
           "#a5b6be", 1.2, refract)
    d.rect("refract-window", 534, 810, 26, 10, "#cfe8ee", "#8fa6b0", .8, 2, refract)
    d.ellipse("refract-drop", 547, 808, 4.5, 4, PEEL, parent=refract)
    d.rect("refract-eye", 562, 826, 8, 8, "#31474f", r=2, parent=refract)

    # Burette over a flask.
    titr = d.group("titration", root)
    d.line("titr-stand", 604, 720, 604, 866, "#8f9ea6", 3, titr)
    d.rect("titr-clamp", 604, 762, 40, 6, "#8f9ea6", r=2, parent=titr)
    d.rect("titr-burette", 636, 726, 9, 84, "#dce8ec", "#9fb2ba", .9, 3, titr)
    d.path("titr-tip", "M 640 810 L 642 828 L 638 828 Z", "#9fb2ba", parent=titr)
    d.path("flask", "M 618 866 L 618 846 L 632 820 L 650 820 L 664 846 L 664 866 Z",
           "#e6f1f3", "#9fb2ba", 1.1, titr)
    d.path("flask-liquid", "M 622 866 L 622 852 L 660 852 L 660 866 Z", "#f3c98a",
           parent=titr, opacity=.85)

    # Data sheet recording the destructive reference assays.
    sheet = d.group("data-sheet", root)
    d.rect("sheet-body", 700, 700, 106, 100, "#ffffff", "#b9c9c6", 1.2, 3, sheet)
    d.rect("sheet-header", 700, 700, 106, 18, "#e6f0ee", r=3, parent=sheet)
    d.text("sheet-title", 708, 713, "Data sheet", 9.2, INK, parent=sheet, bold=True)
    for index in range(4):
        row = 728 + index * 15
        d.line(f"sheet-row-{index + 1}", 708, row, 798, row, "#cfdcda", .9, sheet)
        d.line(f"sheet-entry-{index + 1}", 716, row - 5, 756, row - 5, "#9fb4b0", .8, sheet)
        d.text(f"sheet-unit-{index + 1}", 793, row - 4, "—", 7.6, "#b0c2be",
               anchor="end", parent=sheet)

    # Fruit slice on a sample dish.
    tray = d.group("sample-tray", root)
    d.ellipse("tray-dish", 756, 844, 36, 12, "#eef4f5", "#a9bcc3", 1.2, tray)
    d.ellipse("tray-dish-inner", 756, 843, 30, 8, "#ffffff", parent=tray)
    d.ellipse("slice-peel", 756, 838, 22, 9, PEEL, "#d9a94e", .7, tray)
    d.ellipse("slice-albedo", 756, 838, 19.6, 7.6, ALBEDO, parent=tray)
    d.ellipse("slice-flesh", 756, 838, 17.2, 6.2, FLESH, parent=tray)
    for index in range(5):
        angle = pi * (0.12 + index * 0.19)
        d.line(f"slice-segment-{index + 1}", 756 + 3 * cos(angle), 838 + 1.2 * sin(angle),
               756 + 17 * cos(angle), 838 + 6.2 * sin(angle), "#e59143", .8, tray)

    # Reference metric chips.
    metrics = ("SSC", "TA", "SSC/TA", "Firmness", "Juice content")
    x = 400
    for index, label in enumerate(metrics):
        width = font(9.8).getlength(label) / 16 + 20
        d.rect(f"metric-chip-{index + 1}", x, 906, width, 22, "#ffffff", "#b9d3cf", 1, 11,
               root)
        d.text(f"metric-text-{index + 1}", x + width / 2, 921, label, 9.8, TEAL,
               anchor="middle", parent=root, bold=True)
        x += width + 8
    for label, cx in (("Balance", 458), ("Refractometer", 546),
                      ("Titration", 640), ("Slice", 756)):
        d.text(f"bench-label-{label.lower()}", cx, 898, label, 8.2, MUTED,
               anchor="middle", parent=root)
    return root


def draw_data(d):
    root = d.group("module-d")
    d.rect("data-panel", 836, 122, 528, 856, PANEL, r=14, parent=root)
    module_header(d, "D", "Data Analysis and Quality Prediction", 858, 152, DATA, root)

    steps = ("Spectral matrix", "Pre-processing", "Feature extraction",
             "Model calibration", "Quality prediction")
    x0, width, gap, y = 858, 96, 12, 186
    nodes = []
    for index, label in enumerate(steps):
        x = x0 + index * (width + gap)
        node = d.group(f"pipeline-{index + 1}", root)
        fill = "#ffffff" if index < 4 else "#e6f4f0"
        stroke = "#b6cdd8" if index < 4 else TEAL
        d.rect(f"pipeline-{index + 1}-box", x, y, width, 58, fill, stroke, 1.2, 8, node)
        d.wrap(f"pipeline-{index + 1}-text", x + width / 2, y + 24, label, 8.8,
               width - 16, fill=INK, parent=node, anchor="middle")
        nodes.append((x, y, width))
        if index < 4:
            d.arrow(f"pipeline-arrow-{index + 1}", x + width + 1.5, y + 29,
                    x + width + gap - 2, y + 29, DATA, 1.4, 5, root)

    # Spectral profile card.
    chart = d.group("spectra", root)
    d.rect("spectra-bg", 858, 268, 484, 152, "#ffffff", "#c9dbe4", 1.1, 9, chart)
    d.text("spectra-title", 872, 288, "Spectral profiles", 11.5, INK, parent=chart, bold=True)
    d.line("spectra-x", 876, 396, 1326, 396, "#a9bcc6", 1, chart)
    d.line("spectra-y", 876, 396, 876, 306, "#a9bcc6", 1, chart)
    curves = (("a", 0, 1.0), ("b", 6, .78), ("c", -5, .62))
    for name, shift, opacity in curves:
        points = []
        for step in range(41):
            x = 880 + step * 11
            wave = (sin(step * .42 + shift) * 12 + sin(step * .17 + shift * .5) * 18
                    + cos(step * .09) * 10)
            points.append((x, 352 - wave - shift * 1.2))
        d.path(f"spectra-curve-{name}", "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in points),
               "none", (SIGNAL, TEAL, DATA)[("a", "b", "c").index(name)], 1.9, chart,
               opacity=opacity)

    # Reference/model linkage note.
    link = d.group("calibration", root)
    d.rect("calibration-bg", 858, 436, 484, 58, "#ffffff", "#c9dbe4", 1.1, 9, link)
    d.ellipse("calibration-node-a", 884, 465, 13, 13, "#dceef0", TEAL, 1.2, link)
    d.ellipse("calibration-node-b", 936, 465, 13, 13, "#dceef0", TEAL, 1.2, link)
    d.ellipse("calibration-node-c", 910, 448, 13, 13, "#dceef0", TEAL, 1.2, link)
    d.line("calibration-edge-1", 897, 457, 902, 452, TEAL, 1.2, link)
    d.line("calibration-edge-2", 918, 452, 924, 457, TEAL, 1.2, link)
    d.text("calibration-title", 962, 460, "Calibrated model", 11.5, INK, parent=link, bold=True)
    d.text("calibration-note", 962, 478, "fitted on paired spectra and reference assays",
           9.6, MUTED, parent=link)

    # Individual assessment card.
    one = d.group("individual", root)
    d.rect("individual-bg", 858, 512, 234, 224, "#ffffff", "#c9dbe4", 1.1, 9, one)
    d.text("individual-title", 872, 536, "Individual Fruit", 12, INK, parent=one, bold=True)
    fruit(d, "individual-fruit", 884, 570, 18, FRUIT_RIPE, one, stem=False)
    rows = (("SSC", .74), ("TA", .48), ("Firmness", .62), ("Quality profile", .82))
    for index, (label, value) in enumerate(rows):
        y = 566 + index * 36
        d.text(f"individual-label-{index + 1}", 912, y + 4, label, 9.3, MUTED, parent=one)
        d.rect(f"individual-track-{index + 1}", 912, y + 9, 160, 6, "#e5eef2", r=3, parent=one)
        d.rect(f"individual-bar-{index + 1}", 912, y + 9, 160 * value, 6,
               DATA if index < 3 else TEAL, r=3, parent=one)
    d.text("individual-note", 872, 716, "Predicted per fruit; no numeric claims", 8.8,
           MUTED, parent=one)

    # Batch assessment card.
    batch = d.group("batch", root)
    d.rect("batch-bg", 1108, 512, 234, 224, "#ffffff", "#c9dbe4", 1.1, 9, batch)
    d.text("batch-title", 1122, 536, "Batch Assessment", 12, INK, parent=batch, bold=True)
    d.line("batch-axis", 1122, 640, 1250, 640, "#a9bcc6", 1, batch)
    heights = (.22, .38, .55, .72, .86, .66, .46, .3)
    for index, height in enumerate(heights):
        d.rect(f"batch-bar-{index + 1}", 1128 + index * 13, 640 - 96 * height, 8, 96 * height,
               "#9ec9dd", r=2, parent=batch)
    d.path("batch-curve", "M 1126 628 C 1150 612 1160 588 1180 566 C 1200 544 1220 556 1246 600",
           "none", TEAL, 1.6, batch)
    for index in range(3):
        top = 552 + index * 28
        d.line(f"batch-mean-{index + 1}", 1182, top, 1182, top + 18, TEAL, 1, batch,
               opacity=.45)
    d.text("batch-mean-label", 1178, 547, "mean", 8.2, TEAL, anchor="end",
           parent=batch, bold=True)
    d.text("batch-axis-label", 1186, 656, "fruit-to-fruit variation", 8.8, MUTED,
           anchor="middle", parent=batch)
    d.ellipse("batch-donut", 1290, 594, 32, 32, "none", "#dce8ee", 9, batch)
    d.path("batch-donut-qualified", "M 1290 562 A 32 32 0 0 1 1320 606", "none", TEAL, 9,
           batch)
    d.text("batch-donut-label", 1290, 594, "qualified", 7.8, INK, anchor="middle", parent=batch)
    d.text("batch-proportion", 1290, 605, "proportion", 7.4, MUTED, anchor="middle",
           parent=batch)
    d.text("batch-count-note", 1122, 690, "Counts and shares are illustrative", 8.8,
           MUTED, parent=batch)
    d.text("batch-note", 1122, 706, "and require measured batch data", 8.8, MUTED, parent=batch)

    # Validation loop strip.
    loop = d.group("validation-loop", root)
    d.rect("loop-bg", 858, 756, 484, 132, "#ffffff", "#c9dbe4", 1.1, 9, loop)
    d.text("loop-title", 872, 780, "Calibration and independent validation", 11.5, INK,
           parent=loop, bold=True)
    chips = (("Reference assays", TEAL), ("Paired data", DATA),
             ("Model calibration", DATA), ("Held-out fruit", TEAL))
    x, placed = 872, []
    for index, (label, color) in enumerate(chips):
        width = font(9.4).getlength(label) / 16 + 18
        d.rect(f"loop-chip-{index + 1}", x, 796, width, 24, "#ffffff", color, 1.1, 6, loop)
        d.text(f"loop-text-{index + 1}", x + width / 2, 811, label, 9.4, color,
               anchor="middle", parent=loop, bold=True)
        placed.append((x, x + width))
        x += width + 24
    for index, ((_, end), (start, _)) in enumerate(zip(placed, placed[1:])):
        d.arrow(f"loop-arrow-{index + 1}", end + 2, 808, start - 2, 808, DATA, 1.3, 5, loop)
    d.arrow("loop-return", placed[-1][1], 840, placed[0][0], 840, TEAL, 1.3, 5, loop)
    d.text("loop-return-label", 1094, 856, "validation error returns to the model", 8.8,
           TEAL, anchor="middle", parent=loop)
    return root


def connections(d):
    root = d.group("connections")
    d.arrow("flow-a-b", 356, 330, 392, 330, MUTED, 2.0, 8, root)
    d.arrow("flow-b-c", 600, 604, 600, 622, MUTED, 2.0, 8, root)
    d.arrow("flow-b-d", 812, 214, 846, 214, MUTED, 2.0, 8, root)
    # Metric chips of module C feed the calibration / validation strip in module D.
    d.line("flow-c-d-run", 703, 917, 820, 917, MUTED, 1.6, root)
    d.line("flow-c-d-rise", 820, 917, 820, 812, MUTED, 1.6, root)
    d.arrow("flow-c-d", 820, 808, 855, 808, MUTED, 1.8, 7, root)
    d.text("flow-c-d-label", 758, 940, "reference assays", 8.6, MUTED, anchor="middle",
           parent=root)
    return root


def assemble():
    d = Figure()
    d.group("header")
    d.text("figure-title", 44, 60,
           "Non-destructive Assessment of Citrus Fruit Quality", 26, INK, bold=True,
           parent="header")
    d.text("figure-subtitle", 44, 88,
           "From Orchard Sampling to Predictive Phenotyping", 14.5, MUTED,
           parent="header")
    d.line("header-rule", 44, 106, 1356, 106, RULE, 1.4, "header")
    d.text("figure-scope", 1356, 88,
           "Schematic; no instrument model, wavelength or performance claim", 10.5,
           MUTED, anchor="end", parent="header")

    draw_orchard(d)
    draw_detection(d)
    draw_lab(d)
    draw_data(d)
    connections(d)
    return d


def deliver(project, out):
    """Copy the rendered project and write a readable object-ID inventory."""
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(project / "figure.svg", out / "citrus-quality.svg")
    shutil.copyfile(project / "preview.png", out / "citrus-quality.png")
    scene = json.loads((project / "scene.json").read_text(encoding="utf-8"))
    (out / "citrus-quality.scene.json").write_text(
        json.dumps(scene, ensure_ascii=False, indent=1) + chr(10), encoding="utf-8")

    children = {}
    for obj in scene["objects"]:
        children.setdefault(obj.get("parent"), []).append(obj)
    roots = [obj for obj in scene["objects"] if not obj.get("parent")]

    def detail(obj):
        if obj["kind"] == "text":
            return 'text "%s"' % obj["geometry"]["text"]
        if obj["kind"] in ("rect", "ellipse"):
            return obj["kind"] + " " + ", ".join(
                "%s=%g" % (key, value) for key, value in obj["geometry"].items())
        return obj["kind"]

    lines = ["# Object ID inventory", "",
             "Generated from `scene.json` by `draw.py --deliver`: %d objects, "
             "canvas %dx%d." % (len(scene["objects"]), scene["canvas"]["width"],
                                scene["canvas"]["height"]),
             "",
             "Every ID below is a real Scene object, selectable in the SVG or the "
             "project. Groups keep their children; text objects carry their exact "
             "string; geometry values are local to the parent group.", ""]

    def emit(obj, depth):
        lines.append("%s- `%s` - %s" % ("  " * depth, obj["id"], detail(obj)))
        for child in children.get(obj["id"], []):
            emit(child, depth + 1)

    for root in roots:
        lines.append("## %s" % root["id"])
        lines.append("")
        emit(root, 0)
        lines.append("")
    (out / "OBJECTS.md").write_text(chr(10).join(lines), encoding="utf-8")


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--scale", type=float, default=2)
    parser.add_argument("--deliver", type=Path,
                        help="also copy SVG, PNG, Scene and the object inventory here")
    args = parser.parse_args()
    scene = assemble().scene()
    write_project(scene, args.project, scale=args.scale)
    print(f"wrote {args.project} ({len(scene['objects'])} objects)")
    if args.deliver:
        deliver(args.project, args.deliver)
        print(f"delivered {args.deliver}")

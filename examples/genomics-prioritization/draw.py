"""Redraw the four-panel plant xQTL / candidate-gene prioritisation figure.

The reference is `reference.png` (926 x 1054). The host transcribed its panels,
text and colour roles, then wrote this program; the engine only executes the
geometry. Values shown inside the panels are the reference's own values.

Run:  python draw.py OUTPUT_PROJECT [--scale 3] [--deliver DIR]
"""
import json
import shutil
from argparse import ArgumentParser
from functools import lru_cache
from math import cos, hypot, pi, radians, sin, sqrt
from pathlib import Path

from PIL import ImageFont
from scientific_figure.builder import SceneBuilder
from scientific_figure.fonts import font_info
from scientific_figure.project import write_project

WIDTH, HEIGHT = 926, 1054

INK = "#1f2f36"
BODY = "#33565f"
MUTED = "#5b7b83"
BORDER = "#5fc3d3"
RULE = "#c9d9dd"
HILITE = "#e9f7ea"
RIBBON = "#eef2f3"
TABLE_HEAD = "#8fbdc5"
ROW_GREEN = "#d9ead3"
ROW_BLUE = "#cfe2f3"
TABLE_FOOT = "#9cc6cd"
TEAL_DARK = "#2c6672"
TEAL_MID = "#6cc3d1"
TEAL_PALE = "#bfe4e9"

PINK = "#e0559b"
MAGENTA = "#d61f8c"
ORANGE = "#f2a03d"
BLUE = "#3d7fc1"
NAVY = "#2b3f8f"
RED = "#c8321f"

SPECIES = {
    "A. thaliana": "#f2c4bd",
    "O. sativa": "#b9d9a8",
    "Z. mays": "#c9ccc9",
    "T. aestivum": "#b9c9e8",
    "B. napus": "#f0b366",
    "G. max": "#8fd0c8",
    "S. lycopersicum": "#e05a2b",
    "S. tuberosum": "#f08a24",
    "G. hirsutum": "#dfd9a8",
}


@lru_cache(maxsize=128)
def font(size):
    return ImageFont.truetype(font_info()["path"], round(size * 16))


class Figure:
    def __init__(self):
        self.objects = []
        self.resources = []
        self._serial = 0

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

    def path(self, oid, d, fill="none", stroke=None, width=1, parent=None,
             opacity=None, dash=None):
        self._add(oid, "path", {"d": d}, parent, fill=fill, stroke=stroke,
                  stroke_width=width if stroke else None, opacity=opacity,
                  stroke_dasharray=list(dash) if dash else None)

    def rect(self, oid, x, y, w, h, fill, stroke=None, width=1, r=0, parent=None,
             opacity=None, dash=None):
        geometry = {"x": x, "y": y, "width": w, "height": h}
        if r:
            geometry["rx"] = r
        self._add(oid, "rect", geometry, parent, fill=fill, stroke=stroke,
                  stroke_width=width if stroke else None, opacity=opacity,
                  stroke_dasharray=list(dash) if dash else None)

    def ellipse(self, oid, cx, cy, rx, ry, fill, stroke=None, width=1, parent=None,
                opacity=None, dash=None):
        self._add(oid, "ellipse", {"cx": cx, "cy": cy, "rx": rx, "ry": ry}, parent,
                  fill=fill, stroke=stroke, stroke_width=width if stroke else None,
                  opacity=opacity, stroke_dasharray=list(dash) if dash else None)

    def line(self, oid, x1, y1, x2, y2, stroke=INK, width=1.2, parent=None,
             opacity=None, dash=None):
        self.path(oid, f"M {x1:.2f} {y1:.2f} L {x2:.2f} {y2:.2f}", stroke=stroke,
                  width=width, parent=parent, opacity=opacity, dash=dash)

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
                  stroke=fill if bold else None,
                  stroke_width=max(0.6, size * 0.03) if bold else None)

    def wrap(self, oid, x, y, value, size, width, *, leading=None, fill=INK,
             parent=None, bold=False, anchor="start"):
        leading = leading or size * 1.3
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

    def _head(self, oid, x, y, ux, uy, color, width, head, parent):
        bx, by = x - ux * head, y - uy * head
        half = head * 0.42
        self.path(f"{oid}-tip",
                  f"M {x:.2f} {y:.2f} L {bx - uy * half:.2f} {by + ux * half:.2f} "
                  f"L {bx + uy * half:.2f} {by - ux * half:.2f} Z",
                  color, parent=parent)

    def arrow(self, oid, x1, y1, x2, y2, color=PINK, width=1.4, head=5.5,
              parent=None, opacity=None, dash=None):
        length = hypot(x2 - x1, y2 - y1)
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        self.group(oid, parent)
        self.path(f"{oid}-shaft",
                  f"M {x1:.2f} {y1:.2f} L {x2 - ux * head * .7:.2f} "
                  f"{y2 - uy * head * .7:.2f}", "none", color, width, oid,
                  opacity=opacity, dash=dash)
        self._head(oid, x2, y2, ux, uy, color, width, head, oid)

    def curve(self, oid, points, stroke, width=1.4, parent=None, fill="none",
              opacity=None, dash=None):
        d = f"M {points[0][0]:.2f} {points[0][1]:.2f}"
        for index in range(1, len(points), 3):
            a, b, c = points[index:index + 3]
            d += (f" C {a[0]:.2f} {a[1]:.2f} {b[0]:.2f} {b[1]:.2f} "
                  f"{c[0]:.2f} {c[1]:.2f}")
        self.path(oid, d, fill, stroke, width, parent, opacity, dash)

    def curve_arrow(self, oid, start, control, end, color=PINK, width=1.4,
                    head=5.5, parent=None, opacity=None):
        self.group(oid, parent)
        self.path(f"{oid}-shaft",
                  f"M {start[0]:.2f} {start[1]:.2f} Q {control[0]:.2f} "
                  f"{control[1]:.2f} {end[0]:.2f} {end[1]:.2f}", "none", color,
                  width, oid, opacity=opacity)
        length = hypot(end[0] - control[0], end[1] - control[1])
        ux, uy = (end[0] - control[0]) / length, (end[1] - control[1]) / length
        self._head(oid, end[0], end[1], ux, uy, color, width, head, oid)

    def scene(self):
        builder = SceneBuilder(WIDTH, HEIGHT, background="#ffffff", schema_version="0.3")
        builder.add_many(self.objects, resources=self.resources)
        return builder.build()


# ---------------------------------------------------------------- primitives --
def panel(d, oid, x0, y0, x1, y1, parent=None, width=2.4, radius=10):
    d.rect(oid, x0, y0, x1 - x0, y1 - y0, "#ffffff", BORDER, width, radius, parent)


def header(d, letter, title, x, y, parent=None, width=None):
    size = 13.0
    measured = font(size).getlength(f"{letter}  {title}") / 16
    width = width or measured + 26
    d.rect(f"head-{letter}-ribbon", x - 6, y - size, width, size + 10, HILITE, r=3,
           parent=parent)
    d.text(f"head-{letter}-text", x, y, f"{letter}  {title}", size, INK,
           parent=parent, bold=True)


def species_dot(d, oid, cx, cy, r, species, parent=None, opacity=None):
    d.ellipse(oid, cx, cy, r, r, SPECIES[species], parent=parent, opacity=opacity)


def helix(d, oid, x0, y0, x1, y1, turns, amp, color, width=1.1, rungs=4.5,
          parent=None, rung_color=None):
    """Two intertwined sine strands with short base-pair rungs."""
    length = x1 - x0
    steps = max(24, int(abs(length) / 2))
    top, bottom = [], []
    for index in range(steps + 1):
        t = index / steps
        x = x0 + length * t
        phase = 2 * pi * turns * t
        top.append((x, y0 + amp * sin(phase)))
        bottom.append((x, y0 - amp * sin(phase)))
    def poly(points):
        return "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in points)
    d.path(f"{oid}-strand-a", poly(top), "none", color, width, parent)
    d.path(f"{oid}-strand-b", poly(bottom), "none", color, width, parent)
    count = max(6, int(abs(length) / rungs))
    for index in range(count):
        x = x0 + length * (index + 0.5) / count
        t = (x - x0) / length
        dy = amp * sin(2 * pi * turns * t)
        d.line(f"{oid}-rung-{index + 1}", x, y0 + dy, x, y0 - dy,
               rung_color or color, 0.6, parent)


def wave(d, oid, x0, x1, y, amp, cycles, color, width=1.3, parent=None,
         phase=0.0, opacity=None):
    points = []
    steps = 40
    for index in range(steps + 1):
        t = index / steps
        points.append((x0 + (x1 - x0) * t,
                       y + amp * sin(2 * pi * cycles * t + phase)))
    d.path(oid, "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in points),
           "none", color, width, parent, opacity=opacity)


def bar_cluster(d, oid, x0, y0, widths, color, parent=None, gap=1.4, max_h=20):
    x = x0
    for index, height in enumerate(widths):
        d.rect(f"{oid}-{index + 1}", x, y0 - height, 3.2, height, color, r=0.6,
               parent=parent)
        x += 3.2 + gap


def block_arrow(d, oid, x, y0, y1, width, fill, parent=None, stroke=None):
    shaft = width * 0.42
    head = width * 0.5
    d.path(oid, f"M {x - shaft:.2f} {y0:.2f} L {x + shaft:.2f} {y0:.2f} "
           f"L {x + shaft:.2f} {y1 - head:.2f} L {x + width / 2:.2f} {y1 - head:.2f} "
           f"L {x:.2f} {y1:.2f} L {x - width / 2:.2f} {y1 - head:.2f} "
           f"L {x - shaft:.2f} {y1 - head:.2f} Z", fill, stroke, 0.9, parent)


def quinoa(d, oid, x, y, scale, color, parent=None):
    """Small nucleosome: a squat cylinder."""
    w, h = 13 * scale, 8 * scale
    d.ellipse(f"{oid}-top", x, y - h / 2, w, h * 0.5, color, parent=parent)
    d.rect(f"{oid}-body", x - w, y - h / 2, 2 * w, h, color, r=h / 2, parent=parent)
    d.ellipse(f"{oid}-bottom", x, y + h / 2, w, h * 0.45, color, parent=parent)


# ------------------------------------------------------------------- panel A --
def panel_a(d):
    root = d.group("panel-a")
    panel(d, "a-border", 14, 8, 350, 400, root)
    header(d, "A", "GWAS and QTL mapping.", 24, 32, root)

    table = d.group("gwas-table", root)
    x0, y0, width = 37, 55, 306
    cols = [("SNP1", "SNP1", 140), ("SNP2", "SNP2", 174), ("SNP3", "SNP3", 206),
            ("SNP4", "SNP4", 238), ("dots", "…", 268), ("Trait", "Trait", 318)]
    d.rect("table-shadow", x0 + 3, y0 + 4, width, 120, "#dfe6e6", r=2, parent=table,
           opacity=.6)
    d.rect("table-head", x0, y0, width, 21, TABLE_HEAD, parent=table)
    d.text("table-col-accession", x0 + 8, y0 + 14.5, "", 8.2, "#ffffff", parent=table,
           bold=True)
    for key, label, cx in cols:
        d.text(f"table-col-{key}", cx, y0 + 14.5, label, 8.2, "#ffffff",
               anchor="middle", parent=table, bold=True)
    rows = (("Accession1", ("A", "G", "A", "T", "10"), ROW_GREEN),
            ("Accession2", ("T", "C", "C", "G", "15"), ROW_BLUE),
            ("Accession3", ("A", "G", "C", "G", "10"), ROW_GREEN),
            ("Accession4", ("A", "C", "C", "C", "14"), ROW_BLUE))
    for index, (name, values, fill) in enumerate(rows):
        y = y0 + 21 + index * 19
        d.rect(f"table-row-{index + 1}", x0, y, width, 19, fill, parent=table)
        d.text(f"table-row-{index + 1}-name", x0 + 8, y + 13, name, 7.4, INK,
               parent=table, bold=True)
        for (key, _, cx), value in zip(cols[:4] + cols[5:], values):
            d.text(f"table-row-{index + 1}-{key}", cx, y + 13, value, 7.6,
                   INK, anchor="middle", parent=table)
    d.rect("table-foot", x0, y0 + 97, width, 19, TABLE_FOOT, parent=table)
    for key, _, cx in cols:
        d.text(f"table-foot-{key}", cx, y0 + 110, "…", 8.0, "#2d5d66",
               anchor="middle", parent=table)

    d.text("model-text", 37, 203, "Y = Xβ + Zb + ε", 10.5, INK, parent=root)
    shade = d.linear("arrow-paint", ["#7cc0c8", "#b9dfe3"], start=(0, 0), end=(0, 1))
    block_arrow(d, "model-arrow", 166, 176, 212, 30, shade, root, "#5e9aa1")
    d.text("testing-label", 200, 199, "Association testing", 10.2, INK, parent=root,
           bold=True)

    # Manhattan-like density plot.
    plot = d.group("association-plot", root)
    d.line("plot-axis-y", 55, 226, 55, 352, "#4a5b60", 1.2, plot)
    d.line("plot-axis-x", 55, 352, 344, 352, "#4a5b60", 1.2, plot)
    for index in range(1, 6):
        y = 352 - index * 25
        d.line(f"plot-ytick-{index}", 51, y, 55, y, "#4a5b60", 0.9, plot)
    blocks = 9
    span = (344 - 56) / blocks
    for block in range(blocks):
        color = TEAL_DARK if block % 2 else TEAL_MID
        start = 56 + block * span
        centre = start + span * 0.55
        for step in range(int(span / 1.1)):
            x = start + step * 1.1
            base = (10 + 16 * abs(sin(x * 0.19)) + 12 * abs(sin(x * 0.045 + block))
                    + 8 * abs(sin(x * 1.7)) + 5 * abs(sin(x * 3.1)))
            spike = 34 * pow(2.718, -((x - centre) / 6.0) ** 2)
            height = base + spike
            d.line(f"plot-{block}-{step}", x, 352, x, 352 - height, color, 1.3, plot)
    for index in range(260):
        x = 58 + ((index * 37) % 283)
        base = 18 + 18 * abs(sin(x * 0.19)) + 10 * abs(sin(x * 0.05))
        y = 352 - base - 3 - 16 * abs(sin(index * 2.1))
        fill = RED if (y < 288 and index % 3 == 0) else "#1c2b30"
        d.ellipse(f"plot-dot-{index + 1}", x, y, 0.7, 0.7, fill, parent=plot)
    for index in range(9):
        x = 62 + index * 34
        d.line(f"plot-xtick-{index + 1}", x, 352, x, 356, "#4a5b60", 0.8, plot)
    d.line("plot-threshold", 56, 288, 344, 288, "#6d7d82", 0.9, plot,
           dash=[4, 3])

    # Linkage-disequilibrium triangle over the upper right of the plot.
    heat = d.group("ld-triangle", root)
    d.rect("ld-band", 216, 208, 128, 12, "#141c1f", parent=heat)
    for row in range(11):
        for col in range(row, 11):
            x = 216 + col * 11.6
            y = 220 + row * 8.6
            value = abs(sin((row + 1) * 1.7 + (col + 1) * 0.9))
            red = int(248 - 52 * value)
            green = int(226 - 172 * value)
            blue = int(226 - 172 * value)
            d.rect(f"ld-{row}-{col}", x, y, 11.6, 8.6,
                   f"#{red:02x}{green:02x}{blue:02x}", "#ffffff", 0.6, 0, heat)
    d.path("ld-outline", "M 216 220 L 344 220 L 344 313 Z", "none", "#d94b4b", 1,
           heat)
    d.line("ld-leader", 216, 215, 90, 231, "#e08a4a", 0.8, root, opacity=.9,
           dash=[2, 2])
    return root


# ------------------------------------------------------------------- panel B --
def panel_b(d):
    root = d.group("panel-b")
    panel(d, "b-border", 356, 8, 912, 400, root)
    header(d, "B", "The timeline for association mapping studies in plants.",
           366, 30, root)

    axis_y = 160
    d.line("timeline-axis", 430, axis_y, 880, axis_y, TEAL_DARK, 3.4, root)
    d.path("timeline-head", f"M 892 {axis_y} L 877 {axis_y - 6} L 877 "
           f"{axis_y + 6} Z", TEAL_DARK, parent=root)
    d.text("year-label", 862, 150, "Year", 11, INK, parent=root, bold=True)

    def year_x(year):
        return 432 + (year - 2010) * 24

    years = [2010, 2011, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020,
             2022, 2023, 2024, 2025]
    for year in years:
        x = year_x(year)
        d.line(f"year-tick-{year}", x, axis_y - 4, x, axis_y + 4, TEAL_DARK, 2.0, root)
        d.text(f"year-label-{year}", x - 1, axis_y + 12, str(year), 6.8, INK,
               anchor="end", parent=root, angle=-62, bold=True)

    def radius(value):
        return max(4.0, 0.40 * sqrt(value))

    first = [(2010, "Z. mays", 4892, 445, 64), (2010, "O. sativa", 517, 474, 98),
             (2010, "A. thaliana", 199, 474, 133), (2011, "T. aestivum", 192, 502, 108),
             (2013, "B. napus", 472, 524, 62), (2014, "S. lycopersicum", 163, 552, 130),
             (2015, "T. aestivum", 287, 576, 112), (2016, "B. napus", 355, 600, 116),
             (2017, "S. tuberosum", 67, 624, 136)]
    largest = [(2018, "S. lycopersicum", 442, 634, 205),
               (2018, "Z. mays", 752, 724, 192),
               (2019, "A. thaliana", 1135, 668, 214),
               (2020, "T. aestivum", 7887, 708, 266),
               (2022, "B. napus", 1703, 744, 224),
               (2023, "G. max", 17599, 770, 302),
               (2024, "G. hirsutum", 3724, 806, 272),
               (2025, "G. hirsutum", 3606, 838, 234),
               (2025, "S. tuberosum", 314, 814, 188)]

    bubbles = d.group("timeline-bubbles", root)
    for index, (year, species, value, cx, cy) in enumerate(first):
        r = radius(value)
        d.line(f"stem-first-{index + 1}", year_x(year), axis_y,
               cx, cy + r, SPECIES[species], 0.8, bubbles, opacity=.75)
        species_dot(d, f"bubble-first-{index + 1}", cx, cy, r, species, bubbles,
                    opacity=.92)
        size = 6.6 if value < 1000 else 7.4
        d.text(f"bubble-first-{index + 1}-value", cx, cy + 2.4, f"{value:,}",
               size, "#26343a", anchor="middle", parent=bubbles, bold=True)
    for index, (year, species, value, cx, cy) in enumerate(largest):
        r = radius(value)
        d.line(f"stem-largest-{index + 1}", year_x(year), axis_y,
               cx, cy - r, SPECIES[species], 0.8, bubbles, opacity=.75)
        species_dot(d, f"bubble-largest-{index + 1}", cx, cy, r, species, bubbles,
                    opacity=.92)
        d.text(f"bubble-largest-{index + 1}-value", cx, cy + 2.4, f"{value:,}",
               7.6, "#26343a", anchor="middle", parent=bubbles, bold=True)

    legend = d.group("species-legend", root)
    for index, (name, color) in enumerate(SPECIES.items()):
        y = 200 + index * 17
        d.ellipse(f"legend-dot-{index + 1}", 452, y - 2.2, 4.0, 4.0, color,
                  parent=legend)
        d.text(f"legend-name-{index + 1}", 462, y, name, 7.4, INK, parent=legend)

    sample = d.group("sample-size-legend", root)
    d.rect("sample-box", 762, 92, 128, 40, "#ffffff", "#6a7d84", 1.0, 8, sample)
    for index, r in enumerate((5, 9, 14)):
        d.ellipse(f"sample-ring-{index + 1}", 780, 112, r, r, "none", TEAL_DARK,
                  1.0, sample)
    d.path("sample-arrow", "M 790 112 L 804 112", "none", TEAL_DARK, 1.0, sample)
    d.path("sample-head", "M 806 112 L 800 109 L 800 115 Z", TEAL_DARK, parent=sample)
    d.text("sample-label-1", 810, 108, "Population", 7.4, INK, parent=sample)
    d.text("sample-label-2", 810, 118, "sample size", 7.4, INK, parent=sample)

    d.text("timeline-note-1", 415, 372,
           "Upper panel: First GWAS for each species", 7.8, INK, parent=root)
    d.text("timeline-note-2", 415, 388,
           "Lower panel: GWAS with the largest population size for each species",
           7.8, INK, parent=root)
    return root


# ------------------------------------------------------------------- panel C --
def panel_c(d):
    root = d.group("panel-c")
    panel(d, "c-border", 30, 422, 890, 655, root)
    header(d, "C", "Multiple molecular traits datasets are used for xQTL studies "
           "in plant.", 40, 447, root)

    diagram = d.group("molecular-diagram", root)

    # Chromatin loop that brings the enhancer to the promoter.
    d.path("chromatin-loop", "M 210 516 C 130 500 92 508 80 545 C 70 580 92 600 "
           "150 592 C 172 589 188 584 202 578", "none", "#2b3f70", 1.6, diagram)

    # Promoter and enhancer boxes.
    d.rect("promoter-box", 138, 504, 78, 22, "#eaf3e2", "#5b8c3a", 1.1, 4, diagram)
    d.text("promoter-label", 177, 519, "Promoter", 8.0, INK, anchor="middle",
           parent=diagram, bold=True)
    d.rect("enhancer-box", 135, 568, 84, 22, "#fdf0dc", "#b07a2a", 1.1, 4, diagram)
    d.text("enhancer-label", 177, 583, "Enhancer", 8.0, INK, anchor="middle",
           parent=diagram, bold=True)
    # Transcription-factor blob between the two.
    d.path("tf-blob", "M 150 540 C 150 528 162 524 172 528 C 182 522 194 528 194 538 "
           "C 202 542 200 554 190 558 C 184 566 170 566 164 560 C 152 560 146 550 "
           "150 540 Z", "#a9d6c0", "#6aa98c", 0.9, diagram)
    d.rect("chip-box", 118, 492, 220, 128, "none", "#3d8fbf", 1.0, 6, diagram,
           dash=[4, 3])
    d.text("chip-label-1", 128, 606, "ChIP-seq   CAGE-seq", 7.0, "#c0504d",
           parent=diagram)
    d.text("chip-label-2", 128, 618, "ATAC-seq  CUT&Tag", 7.0, "#c0504d",
           parent=diagram)

    # Nucleosomes with methylation and acetylation marks.
    quinoa(d, "nucleosome-1", 288, 506, 1.0, "#6fb3c9", diagram)
    quinoa(d, "nucleosome-2", 312, 512, 1.0, "#5aa3bd", diagram)
    quinoa(d, "nucleosome-3", 330, 520, 0.9, "#6fb3c9", diagram)
    d.line("me-stem", 288, 496, 278, 480, "#8b3c36", 0.9, diagram)
    d.ellipse("me-mark", 276, 477, 6, 6, "#b03a34", parent=diagram)
    d.text("me-label", 276, 479.4, "Me", 4.6, "#ffffff", anchor="middle",
           parent=diagram, bold=True)
    d.line("ac-stem", 326, 508, 334, 485, "#5b3f8f", 0.9, diagram)
    d.ellipse("ac-mark", 336, 480, 6, 6, "#6b4fa0", parent=diagram)
    d.text("ac-label", 336, 482.4, "Ac", 4.6, "#ffffff", anchor="middle",
           parent=diagram, bold=True)

    # Open chromatin and the gene body.
    helix(d, "dna-helix", 352, 534, 520, 534, 7, 5, TEAL_DARK, 1.0, 4.0, diagram)
    d.line("polymerase-stem", 398, 512, 398, 528, TEAL_DARK, 1.0, diagram)
    d.ellipse("polymerase-mark", 398, 510, 3.4, 3.4, "#4c9fc0", parent=diagram)
    d.rect("ont-box", 345, 468, 180, 74, "none", "#5bb37a", 1.0, 6, diagram,
           dash=[4, 3])
    d.text("ont-label-1", 430, 516, "ONT/PacBio", 7.2, "#a63a63", parent=diagram)
    d.text("ont-label-2", 430, 527, "WGBS", 7.2, "#a63a63", parent=diagram)

    # RNA polymerase II, painted first so the gene box stays readable on top.
    d.path("rnap-body", "M 540 520 C 528 496 552 484 576 490 C 590 474 614 478 "
           "622 494 C 642 490 656 506 650 524 C 664 536 656 556 638 560 "
           "C 636 578 614 586 600 572 C 584 584 562 578 558 562 "
           "C 540 560 530 538 540 520 Z", "#b7dcf3", "#7cc7e8", 1.2, diagram,
           opacity=.92)
    gene = d.group("gene-model", diagram)
    d.rect("gene-box", 560, 522, 92, 22, "#eef4d8", "#6b8f3a", 1.1, 4, gene)
    d.text("gene-label", 606, 537, "Gene", 8.0, INK, anchor="middle", parent=gene,
           bold=True)

    # Nascent transcript.
    d.path("nascent-rna", "M 566 552 C 552 572 540 586 536 600 C 528 614 512 618 "
           "502 612", "none", PINK, 2.0, diagram)
    d.path("nascent-branch", "M 540 590 C 548 598 556 604 564 606", "none", PINK,
           1.6, diagram)
    d.ellipse("rna-cap", 494, 606, 3.2, 3.2, "#e0407c", parent=diagram)

    d.rect("transcription-box", 438, 574, 120, 54, "none", "#9a4d93", 1.0, 6,
           diagram, dash=[4, 3])
    d.text("transcription-title", 448, 588, "Transcription", 7.8, INK,
           parent=diagram, bold=True)
    d.text("transcription-rna", 448, 601, "RNA-seq", 7.0, "#a63a63", parent=diagram)
    d.text("transcription-iso", 448, 613, "ISO-seq", 7.0, "#a63a63", parent=diagram)
    d.text("transcription-merip", 502, 613, "MeRIP-seq", 7.0, "#a63a63",
           parent=diagram)

    # Mature mRNA: a chain of hairpins leaving the nucleus.
    for index in range(6):
        x = 660 + index * 13
        d.path(f"mrna-loop-{index + 1}", f"M {x:.1f} 528 C {x + 2:.1f} 518 "
               f"{x + 11:.1f} 518 {x + 11:.1f} 528 C {x + 11:.1f} 538 "
               f"{x + 2:.1f} 538 {x:.1f} 528", "none", "#3f8fc0", 1.4, diagram)

    # Translation.
    d.rect("translation-box", 662, 566, 118, 64, "none", "#e0a23a", 1.0, 6,
           diagram, dash=[4, 3])
    d.text("translation-title", 670, 581, "Translation", 7.8, INK, parent=diagram,
           bold=True)
    d.text("translation-ribo", 670, 595, "Ribo-seq", 7.0, "#a63a63", parent=diagram)
    d.text("translation-ms", 670, 607, "MS/MS", 7.0, "#a63a63", parent=diagram)
    d.text("translation-lc", 736, 595, "LC/MS", 7.0, "#a63a63", parent=diagram)
    d.text("translation-gc", 736, 607, "GC/MS", 7.0, "#a63a63", parent=diagram)

    # Ribosome, peptide chain and the protein product sit above the assay box.
    d.ellipse("ribosome-large", 706, 552, 11, 9, "#7fb98b", parent=diagram)
    d.ellipse("ribosome-small", 706, 541, 8, 5, "#9ccfa4", parent=diagram)
    d.path("peptide", "M 718 546 C 724 540 730 548 736 541 C 741 536 747 542 "
           "752 537", "none", "#2f5d9e", 1.8, diagram)
    for index in range(5):
        d.ellipse(f"protein-unit-{index + 1}", 754 + index * 4.2, 537 - index * 1.2,
                  2.2, 2.2, "#2f5d9e", parent=diagram)
    d.text("protein-label", 748, 528, "Protein", 7.8, INK, parent=diagram, bold=True)

    # Genomic DNA and the sequencing assays in the far-right cell.
    d.ellipse("cell-body", 878, 520, 24, 36, "#dbeefb", "#9ccbe4", 0.9, root)
    d.ellipse("nucleus", 878, 520, 14, 17, "#bcd9f2", "#7fa8c9", 0.8, root)
    d.ellipse("nucleolus", 878, 520, 5.5, 6.5, "#93b9dc", parent=root)
    d.rect("dna-box", 788, 480, 80, 84, "none", "#6f7d82", 1.0, 8, root, dash=[4, 3])
    d.text("dna-label", 796, 500, "DNA", 8.0, INK, parent=root, bold=True)
    d.text("dna-note-1", 796, 542, "WGS/WES", 7.0, "#a63a63", parent=root)
    d.text("dna-note-2", 796, 554, "ONT/PacBio", 7.0, "#a63a63", parent=root)

    # A subtle double helix inside the dashed DNA box.
    helix(d, "dna-box-helix", 798, 522, 860, 522, 3, 3, "#8fb7d6", 0.7, 3.0, root)
    return root


# ------------------------------------------------------------------- panel D --
def qtl_panel(d, oid, x, y, label, caption, kind, parent):
    box = d.group(oid, parent)
    d.text(f"{oid}-label", x, y, label, 10.0, INK, parent=box, bold=True)
    base_y = y + 34
    d.line(f"{oid}-axis", x + 2, base_y, x + 188, base_y, "#20303a", 1.2, box)
    d.ellipse(f"{oid}-snp", x + 18, base_y, 3.6, 3.6, "#e8792b", parent=box)
    d.text(f"{oid}-snp-label", x + 4, base_y + 13, "SNP", 6.8, INK, parent=box)
    start = (x + 22, base_y - 2)
    end = (x + 96, base_y - 12)
    d.curve_arrow(f"{oid}-effect", start, (x + 50, base_y - 26), end, PINK, 1.3, 4.5,
                  box)

    if kind in ("expression", "accessibility", "abundance"):
        if kind == "accessibility":
            d.rect(f"{oid}-gene", x + 80, base_y - 6, 76, 10, "#dbe8f4", "#4d7ea8",
                   0.8, 2, box)
            d.path(f"{oid}-gene-arrow", f"M {x + 156} {base_y - 6} L {x + 166} "
                   f"{base_y - 1} L {x + 156} {base_y + 4} Z", "#4d7ea8", parent=box)
            bar_cluster(d, f"{oid}-hist", x + 82, base_y - 8,
                        [4, 7, 8, 12, 20, 15, 10, 7, 9, 30, 34, 22], "#3d7fb5", box,
                        gap=1.0)
        elif kind == "abundance":
            d.rect(f"{oid}-gene", x + 78, base_y - 6, 78, 10, "#dbe8f4", "#4d7ea8",
                   0.8, 2, box)
            d.path(f"{oid}-gene-arrow", f"M {x + 156} {base_y - 6} L {x + 166} "
                   f"{base_y - 1} L {x + 156} {base_y + 4} Z", "#4d7ea8", parent=box)
            for row in range(2):
                for col in range(5):
                    d.rect(f"{oid}-protein-{row}-{col}", x + 104 + col * 8,
                           base_y - 18 - row * 7, 6, 5, "#3f74b8", r=2, parent=box)
        else:
            d.rect(f"{oid}-gene", x + 78, base_y - 6, 78, 10, "#dbe8f4", "#4d7ea8",
                   0.8, 2, box)
            d.path(f"{oid}-gene-arrow", f"M {x + 156} {base_y - 6} L {x + 166} "
                   f"{base_y - 1} L {x + 156} {base_y + 4} Z", "#4d7ea8", parent=box)
            for index, color in enumerate(("#3d7fb5", "#4f93c8", "#2f6da8")):
                wave(d, f"{oid}-expr-{index + 1}", x + 78, x + 166,
                     base_y - 18 - index * 6, 4.5, 3.2, color, 0.9, box,
                     phase=index * 0.6)

    if kind == "splicing":
        d.rect(f"{oid}-gene", x + 78, base_y - 6, 82, 10, "#dbe8f4", "#4d7ea8",
               0.8, 2, box)
        d.path(f"{oid}-gene-arrow", f"M {x + 160} {base_y - 6} L {x + 170} "
               f"{base_y - 1} L {x + 160} {base_y + 4} Z", "#4d7ea8", parent=box)
        for index, color in enumerate(("#e8873a", "#4e9a58", "#3f74b8", "#c04b9a")):
            d.rect(f"{oid}-exon-{index + 1}", x + 84 + index * 16, base_y - 5, 8, 8,
                   color, parent=box)
            d.path(f"{oid}-splice-{index + 1}", f"M {x + 88 + index * 16} "
                   f"{base_y - 8} C {x + 96 + index * 16} {base_y - 22} "
                   f"{x + 104 + index * 16} {base_y - 22} {x + 110 + index * 16} "
                   f"{base_y - 9}", "none", color, 1.1, box)

    if kind == "cpg":
        d.rect(f"{oid}-cpg", x + 78, base_y - 5, 82, 9, "#dbe8f4", "#4d7ea8", 0.8,
               2, box)
        for index in range(4):
            d.line(f"{oid}-cpg-stem-{index + 1}", x + 88 + index * 19,
                   base_y - 5, x + 88 + index * 19, base_y - 15, "#6b8fa8", 0.9, box)
            d.ellipse(f"{oid}-cpg-dot-{index + 1}", x + 88 + index * 19,
                      base_y - 17, 2.2, 2.2, "#9cc6a8", parent=box)
        d.path(f"{oid}-me-hex", f"M {x + 130} {base_y - 30} l 8 0 l 4 7 l -4 7 "
               f"l -8 0 l -4 -7 Z", "#9cc6a8", "#4f7d5c", 0.8, box)
        d.text(f"{oid}-me-label", x + 134, base_y - 20, "Me", 6.4, "#1f3d2a",
               anchor="middle", parent=box)

    if kind == "cre":
        d.rect(f"{oid}-cre", x + 78, base_y - 5, 82, 9, "#dbe8f4", "#4d7ea8", 0.8,
               2, box)
        for index in range(3):
            d.rect(f"{oid}-cre-box-{index + 1}", x + 84 + index * 22, base_y - 5,
                   10, 9, "#a9c4de", "#4d7ea8", 0.7, 1, box)
        d.ellipse(f"{oid}-tf-1", x + 112, base_y - 22, 11, 6, "#8ec6c9", parent=box)
        d.ellipse(f"{oid}-tf-2", x + 136, base_y - 16, 10, 6, "#7fb9c0", parent=box)
        d.ellipse(f"{oid}-tf-3", x + 147, base_y - 25, 6, 5, "#3f74b8", parent=box)
        d.text(f"{oid}-tf-label", x + 124, base_y - 28, "TF", 6.8, INK,
               anchor="middle", parent=box, bold=True)

    if kind == "histone":
        d.path(f"{oid}-dna", f"M {x + 78} {base_y} C {x + 108} {base_y - 10} "
               f"{x + 140} {base_y + 10} {x + 172} {base_y}", "none", "#20303a",
               1.2, box)
        d.ellipse(f"{oid}-nucleosome", x + 128, base_y - 3, 12, 8, "#a9d3de", "#5b8fa0",
                  0.9, box)
        d.path(f"{oid}-tail-1", f"M {x + 124} {base_y - 8} C {x + 118} "
               f"{base_y - 20} {x + 124} {base_y - 26} {x + 132} {base_y - 30}",
               "none", "#7fa8b8", 1.0, box)
        d.path(f"{oid}-tail-2", f"M {x + 132} {base_y - 9} C {x + 138} "
               f"{base_y - 21} {x + 132} {base_y - 27} {x + 126} {base_y - 32}",
               "none", "#7fa8b8", 1.0, box)
        for index in range(2):
            d.ellipse(f"{oid}-ac-{index + 1}", x + 132 + index * 12,
                      base_y - 34, 5, 5, "#f0d24a", "#b79a1f", 0.6, box)
            d.text(f"{oid}-ac-label-{index + 1}", x + 132 + index * 12,
                   base_y - 32.6, "Ac", 4.2, "#4a3c00", anchor="middle", parent=box,
                   bold=True)

    d.text(f"{oid}-caption", x + 96, base_y + 13, caption, 6.9, INK,
           anchor="middle", parent=box)


def panel_d(d):
    root = d.group("panel-d")
    panel(d, "d-border", 30, 665, 890, 1046, root)
    header(d, "D", "Candidate gene prioritization by integration of xQTL.", 40, 690,
           root)

    qtl_panel(d, "eqtl", 40, 712, "eQTL", "Gene expression", "expression", root)
    qtl_panel(d, "caqtl", 248, 712, "caQTL", "Chromatin accessibility",
              "accessibility", root)
    qtl_panel(d, "sqtl", 40, 787, "sQTL", "Alternative splicing", "splicing", root)
    qtl_panel(d, "pqtl", 248, 787, "pQTL", "Protein abundance", "abundance", root)
    qtl_panel(d, "meqtl", 40, 862, "meQTL", "CpG", "cpg", root)
    qtl_panel(d, "mqtl", 248, 862, "mQTL", "Metabolites abundance", "expression", root)
    d.text("mqtl-metabolite", 360, 851, "Metabolite", 6.4, "#7a3f8f", parent=root,
           bold=True)
    qtl_panel(d, "bqtl", 40, 937, "bQTL", "CRE", "cre", root)
    qtl_panel(d, "haqtl", 248, 937, "haQTL", "", "histone", root)

    # TWAS.
    twas = d.group("twas", root)
    d.text("twas-title", 470, 712, "TWAS", 10.5, INK, parent=twas, bold=True)
    for index, (label, x, width) in enumerate((("Training", 474, 74),
                                               ("Imputation", 580, 84),
                                               ("Association", 692, 88))):
        d.rect(f"twas-box-{index + 1}", x, 724, width, 30, "#ffffff", "#7fc4bd",
               1.2, 2, twas)
        d.text(f"twas-label-{index + 1}", x + width / 2, 743, label, 8.0, INK,
               anchor="middle", parent=twas)
        if index < 2:
            d.arrow(f"twas-arrow-{index + 1}", x + width + 3, 739,
                    x + width + 13, 739, "#8fb9c9", 1.2, 4, twas)

    # Colocalization.
    col = d.group("colocalization", root)
    d.text("coloc-title", 470, 782, "Colocalization", 10.5, INK, parent=col,
           bold=True)
    d.ellipse("coloc-trait-1", 512, 812, 38, 15, "#ffffff", "#6f63a8", 1.1, col)
    d.text("coloc-trait-1-label", 512, 815, "Trait 1", 8.0, INK, anchor="middle",
           parent=col)
    d.ellipse("coloc-trait-2", 720, 812, 38, 15, "#ffffff", "#a86fa0", 1.1, col)
    d.text("coloc-trait-2-label", 720, 815, "Trait 2", 8.0, INK, anchor="middle",
           parent=col)
    d.text("coloc-causal", 616, 806, "Causal GV", 7.6, INK, parent=col, bold=True)
    helix(d, "coloc-helix", 552, 850, 690, 850, 4, 6, MAGENTA, 1.4, 4.0, col,
          rung_color="#e05fb4")
    d.rect("coloc-variant", 612, 838, 9, 22, "#e8823a", "#b55c17", 0.7, 1, col)
    d.arrow("coloc-link-1", 540, 824, 604, 845, "#6f8fc0", 1.0, 4, col)
    d.arrow("coloc-link-2", 694, 824, 628, 845, "#6f8fc0", 1.0, 4, col)

    # Mendelian randomisation.
    mr = d.group("mr", root)
    d.text("mr-title-1", 466, 900, "Summary-data-based", 10.5, INK, parent=mr,
           bold=True)
    d.text("mr-title-2", 466, 913, "Mendelian Randomization", 10.5, INK, parent=mr,
           bold=True)
    d.ellipse("mr-exposure", 512, 958, 44, 15, "#ffffff", "#6f63a8", 1.1, mr)
    d.text("mr-exposure-label", 512, 961, "Exposure", 8.0, INK, anchor="middle",
           parent=mr)
    d.ellipse("mr-trait", 716, 958, 36, 15, "#ffffff", "#a86fa0", 1.1, mr)
    d.text("mr-trait-label", 716, 961, "Trait", 8.0, INK, anchor="middle", parent=mr)
    d.arrow("mr-arrow-1", 558, 952, 678, 952, "#6f8fc0", 1.1, 4, mr)
    d.arrow("mr-arrow-2", 584, 990, 536, 974, "#6f8fc0", 1.0, 4, mr)
    d.arrow("mr-arrow-3", 648, 990, 700, 974, "#6f8fc0", 1.0, 4, mr)
    helix(d, "mr-helix", 548, 1004, 694, 1004, 4, 6, MAGENTA, 1.4, 4.0, mr,
          rung_color="#e05fb4")
    for index in range(4):
        d.rect(f"mr-variant-{index + 1}", 574 + index * 24, 992, 9, 22, "#e8823a",
               "#b55c17", 0.7, 1, mr)

    # Large curved transfers between the mini-QTL block and the integration block.
    for index, (x, flip) in enumerate(((452, 1), (770, -1))):
        d.path(f"transfer-{index + 1}",
               f"M {x} {888} C {x + 14 * flip} 872 {x + 14 * flip} 858 "
               f"{x} 842", "none", "#8fcbc8", 5.0, root, opacity=.85)
        d.path(f"transfer-{index + 1}-head",
               f"M {x} 836 L {x - 7} 850 L {x + 7} 850 Z", "#8fcbc8", parent=root)

    # Right-hand tool column.
    tools = d.group("tool-column", root)
    d.rect("tools-bg", 798, 678, 84, 354, "#ffffff", "#7fc4bd", 1.3, 6, tools)
    d.text("tools-title-1", 806, 702, "Prioritize", 10.0, INK, parent=tools,
           bold=True)
    d.text("tools-title-2", 806, 716, "‘effector gene’", 10.0, INK, parent=tools,
           bold=True)
    blocks = (("xQTL", ("RicePilaf, PoPS,", "MSC, SMR-Portal"), 728, 62),
              ("Machine learning", ("FLAMES, EMS, Open", "Targets Platform,",
                                    "Iswine, QTG-LGBM,", "GARFIELD"), 796, 82),
              ("Statistical scores", ("DEPICT, POCKET,", "MAGMA"), 884, 64))
    for index, (title, lines, y, height) in enumerate(blocks):
        d.rect(f"tool-box-{index + 1}", 804, y, 72, height, "#ffffff", "#6fa9b6",
               1.2, 8, tools)
        d.text(f"tool-title-{index + 1}", 810, y + 14, title, 7.8, INK,
               parent=tools, bold=True)
        for line_index, line in enumerate(lines):
            d.text(f"tool-{index + 1}-line-{line_index + 1}", 810,
                   y + 26 + line_index * 9, line, 6.1, INK, parent=tools)
    return root


# ------------------------------------------------------------------- compose --
def assemble():
    d = Figure()
    panel_a(d)
    panel_b(d)
    panel_c(d)
    panel_d(d)
    return d


def deliver(project, out):
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(project / "figure.svg", out / "redraw.svg")
    shutil.copyfile(project / "preview.png", out / "redraw.png")
    scene = json.loads((project / "scene.json").read_text(encoding="utf-8"))
    (out / "redraw.scene.json").write_text(
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
             "Every ID is a real Scene object. Groups keep their children; text "
             "objects carry their exact string; geometry values are local to the "
             "parent group.", ""]

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
    parser.add_argument("--scale", type=float, default=3)
    parser.add_argument("--deliver", type=Path,
                        help="also copy SVG, PNG, Scene and the object inventory here")
    args = parser.parse_args()
    scene = assemble().scene()
    write_project(scene, args.project, scale=args.scale)
    print(f"wrote {args.project} ({len(scene['objects'])} objects)")
    if args.deliver:
        deliver(args.project, args.deliver)
        print(f"delivered {args.deliver}")

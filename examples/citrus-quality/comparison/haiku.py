"""Build an editable citrus-quality workflow from original vector geometry."""
from argparse import ArgumentParser
from math import cos, sin, sqrt, radians
from pathlib import Path

from scientific_figure.builder import SceneBuilder
from scientific_figure.project import write_project

PAPER = "#f8f6ed"
CARD = "#fffdf7"
INK = "#203a31"
MUTED = "#4c6358"
LINE = "#d7dfd0"
GREEN = "#38634f"
PALE = "#e9f0e4"
GOLD = "#a95b2b"
WARM = "#f8ebd7"
WOOD = "#796046"

objects = []
resources = []


def node(oid, kind, geometry=None, parent=None, transform=None, **style):
    item = {"id": oid, "kind": kind}
    if geometry is not None:
        item["geometry"] = geometry
    if parent is not None:
        item["parent"] = parent
    if transform is not None:
        item["transform"] = transform
    if style:
        item["style"] = style
    objects.append(item)
    return oid


def group(oid, parent=None, x=0, y=0, scale=1, angle=0):
    c, s = cos(radians(angle)) * scale, sin(radians(angle)) * scale
    return node(oid, "group", parent=parent, transform=[c, s, -s, c, x, y])


def rect(parent, key, x, y, w, h, fill, stroke="none", r=0):
    node(f"{parent}-{key}", "rect", dict(x=x, y=y, width=w, height=h, rx=r), parent,
         fill=fill, stroke=stroke, stroke_width=1.2)


def path(parent, key, d, fill="none", stroke="none", width=1, **style):
    node(f"{parent}-{key}", "path", {"d": d}, parent, fill=fill, stroke=stroke,
         stroke_width=width, **style)


def ellipse(parent, key, x, y, rx, ry, fill, **style):
    node(f"{parent}-{key}", "ellipse", dict(cx=x, cy=y, rx=rx, ry=ry), parent,
         fill=fill, **style)


def text(parent, key, x, y, label, size=17, color=INK, anchor="start"):
    node(f"{parent}-{key}", "text", dict(x=x, y=y, text=label), parent,
         fill=color, font_size=size, anchor=anchor)


def arrow(parent, key, x1, y1, x2, y2, color=GREEN):
    length = sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
    dx, dy = (x2 - x1) / length, (y2 - y1) / length
    bx, by = x2 - dx * 8, y2 - dy * 8
    path(parent, key + "-shaft", f"M{x1} {y1} L{bx:.2f} {by:.2f}", stroke=color, width=1.7)
    path(parent, key + "-head", f"M{x2} {y2} L{bx - dy * 4:.2f} {by + dx * 4:.2f} "
         f"L{bx + dy * 4:.2f} {by - dx * 4:.2f} Z", fill=color)


def gradient(key, colors):
    resources.append(dict(id=key, kind="radial_gradient", center=[0.41, 0.40],
                          radius=0.70, focus=[0.30, 0.25],
                          stops=list(zip([0, 0.50, 1], colors))))
    return f"url(#{key})"


PEEL = {
    "green": (gradient("peel-green", ["#e6e8a4", "#9fa951", "#57753b"]), "#54703d"),
    "turning": (gradient("peel-turning", ["#f8e8a5", "#e4b254", "#9a853b"]), "#8b743c"),
    "orange": (gradient("peel-orange", ["#ffdf94", "#ee9b43", "#b95e27"]), "#a35c30"),
}
LEAF = gradient("leaf-green", ["#c6d08a", "#789453", "#315b43"])


def leaf(oid, parent, x, y, scale, angle):
    p = group(oid, parent, x, y, scale, angle)
    path(p, "blade", "M0 0 C15 -22 49 -25 77 -4 C57 14 24 21 0 0 Z", LEAF, "#46694a", 1.1)
    path(p, "midrib", "M-5 2 Q30 -5 72 -4", stroke="#456a48", width=1.1)
    for i, pos in enumerate((15, 25, 36, 47, 57)):
        tip = pos + 11
        path(p, f"veins-{i}", f"M{pos} -2 Q{pos + 4} -8 {tip} {-17 + i * 0.7} "
             f"M{pos} -2 Q{pos + 4} 3 {tip} {10 - i * 0.65}",
             stroke="#597b4f", width=0.7, stroke_opacity=0.78)


def fruit(oid, parent, x, y, scale, stage, texture=28):
    p = group(oid, parent, x, y, scale)
    paint, edge = PEEL[stage]
    path(p, "peel", "M0 -39 C20 -42 40 -28 43 -6 C48 18 30 40 7 42 "
         "C-15 44 -38 31 -42 11 C-48 -13 -34 -35 -16 -38 Q-6 -41 0 -39 Z", paint, edge, 1.2)
    for i in range(texture):
        theta = i * 2.39996323
        radius = 34 * sqrt((i + 0.5) / texture)
        px, py = radius * cos(theta), radius * sin(theta)
        ellipse(p, f"pore-{i}", round(px, 2), round(py, 2), 0.7, 0.55,
                edge, opacity=0.32 + 0.13 * (i % 2))
    path(p, "peel-highlight", "M-32 -15 Q-28 -27 -18 -30", stroke="#fff4c9",
         width=1.6, stroke_opacity=0.65)
    path(p, "calyx", "M-10 -37 Q-4 -40 0 -39 Q5 -42 11 -38 Q4 -34 1 -35 Q-3 -34 -10 -37 Z", "#627747")
    path(p, "stem", "M0 -37 Q5 -45 3 -49", stroke=WOOD, width=2.3)


def tag(parent, key, x, y, label, fill=CARD):
    rect(parent, key + "-paper", x - 24, y - 14, 48, 21, fill, LINE, 4)
    text(parent, key + "-id", x, y + 1, label, 13, INK, "middle")


def panel(oid, x, number, title, subtitle):
    p = group(oid, x=x, y=143)
    rect(p, "paper", 0, 0, 346, 566, CARD, LINE, 13)
    rect(p, "number-bg", 18, 18, 31, 24, PALE, r=5)
    text(p, "number", 33.5, 35, number, 14, GREEN, "middle")
    text(p, "title", 59, 39, title, 25)
    text(p, "subtitle", 19, 69, subtitle, 15, MUTED)
    path(p, "rule", "M19 84 H327", stroke=LINE)
    return p


def document(oid, parent, x, y, color):
    p = group(oid, parent, x, y)
    path(p, "page", "M1 1 H18 L25 8 V31 H1 Z M18 1 V8 H25", CARD, color, 1.4)
    path(p, "rows", "M6 14 H20 M6 19 H20 M6 24 H16", stroke=color, width=1.3)


def orchard():
    p = panel("orchard", 48, "01", "Orchard sampling", "Tag fruit across maturity stages")
    ellipse(p, "botanical-field", 173, 209, 147, 110, "#f4f5e9")
    path(p, "main-branch", "M23 200 C62 174 105 161 154 148 C196 138 249 117 304 108 "
         "C252 124 206 146 156 158 C110 169 65 187 26 213 Z", "#877053", "#65563d", 1.1)
    path(p, "branch-grain", "M30 200 Q80 174 152 153 Q213 138 251 123", stroke="#b49a73", width=1.4)
    path(p, "side-shoots", "M73 180 Q86 154 66 136 M230 127 Q247 117 253 104",
         stroke=WOOD, width=2.6)
    leaf("orchard-leaf-top-left", p, 68, 143, 0.72, 206)
    leaf("orchard-leaf-upper", p, 96, 171, 0.95, -76)
    leaf("orchard-leaf-center", p, 156, 148, 0.86, -38)
    leaf("orchard-leaf-end", p, 254, 119, 0.87, -19)
    leaf("orchard-leaf-right", p, 246, 151, 0.83, 16)
    leaf("orchard-leaf-left", p, 71, 182, 0.61, 152)
    leaf("orchard-leaf-low", p, 188, 152, 0.64, 51)
    path(p, "fruit-stems", "M76 182 Q86 186 87 207 M127 161 C145 169 171 190 171 218 "
         "M200 139 C230 146 267 172 265 199", stroke=WOOD, width=2.6)
    specimens = [("C01", 85, 238, .72, "green"), ("C02", 169, 253, .77, "turning"),
                 ("C03", 262, 238, .87, "orange")]
    for label, x, y, scale, stage in specimens:
        fruit(f"branch-{label}", p, x, y, scale, stage)
        path(p, f"leader-{label}", f"M{x} {y + 40 * scale:.1f} V303", stroke=MUTED)
        tag(p, f"branch-tag-{label}", x, 318, label)
    arrow(p, "to-crate", 173, 339, 173, 376)
    ellipse(p, "crate-shadow", 173, 501, 116, 14, "#ede8d8")
    path(p, "crate-interior", "M57 416 L81 394 H267 L289 416 V488 H57 Z", "#bc8e5f", WOOD, 1.5)
    for (label, _, _, _, stage), x in zip(specimens, (103, 173, 243)):
        fruit(f"crate-{label}", p, x, 425, .54, stage, 11)
    path(p, "crate-front", "M57 431 H289 L278 492 H68 Z", "#d9b484", "#987550", 1.5)
    rect(p, "crate-top-slat", 53, 427, 240, 12, "#e6c69b", "#a7835b", 2)
    path(p, "crate-grain", "M67 448 H281 M70 482 H277", stroke="#b59065", width=1.1)
    for label, x in (("C01", 103), ("C02", 173), ("C03", 243)):
        tag(p, f"crate-tag-{label}", x, 468, label, "#fff4db")
    text(p, "crate-label", 173, 534, "Sampling crate", 18, INK, "middle")


def scanning():
    p = panel("scan", 434, "02", "Optical scanning", "Scan each intact fruit")
    ellipse(p, "instrument-field", 173, 221, 145, 111, "#f0f4ec")
    ellipse(p, "fruit-shadow", 166, 307, 69, 12, "#dfe8db")
    fruit("scan-intact-C02", p, 163, 253, 1.29, "turning", 39)
    path(p, "probe-cable", "M218 112 C249 91 286 99 296 120", stroke=GREEN, width=3.3)
    pbody = group("optical-probe", p, 206, 151, 1, 31)
    rect(pbody, "housing", -19, -43, 38, 68, "#446a60", INK, 9)
    rect(pbody, "inset", -11, -33, 22, 31, "#75978b", r=4)
    rect(pbody, "indicator", -5, -26, 10, 5, "#dce8bd", r=2)
    rect(pbody, "collar", -16, 14, 32, 16, "#9fb6a9", GREEN, 3)
    rect(pbody, "tip", -9, 30, 18, 9, "#2b4e44", r=2)
    path(p, "illumination", "M181 180 L166 204 Q180 205 190 213 L190 184 Z", "#f1b96d", opacity=0.45)
    path(p, "probe-leader", "M234 154 H255 V175", stroke=MUTED)
    text(p, "probe-label", 256, 194, "Optical probe", 14, MUTED, "middle")
    tag(p, "scanned-tag", 163, 327, "C02")
    arrow(p, "save", 173, 344, 173, 373)
    rect(p, "records-card", 19, 382, 308, 153, "#f7faf3", LINE, 8)
    document("spectral-document", p, 32, 397, GREEN)
    text(p, "record-title", 69, 419, "Saved spectra", 20)
    for i, label in enumerate(("C01", "C02", "C03")):
        y = 451 + i * 30
        path(p, f"record-rule-{label}", f"M33 {y - 17} H313", stroke=LINE)
        text(p, f"spectrum-id-{label}", 34, y + 3, label, 15, GREEN)
        text(p, f"spectrum-label-{label}", 95, y + 3, "Spectrum record", 15, MUTED)


def assay_icon(oid, parent, x, y, fill, stroke):
    p = group(oid, parent, x, y)
    path(p, "drop", "M0 -13 C-3 -8 -11 -1 -11 5 A11 11 0 0 0 11 5 C11 -1 3 -8 0 -13 Z",
         fill, stroke, 1.4)
    path(p, "highlight", "M-6 4 Q-7 8 -3 10", stroke=CARD, width=1.3)


def reference():
    p = panel("reference", 820, "03", "Reference assays", "Select fruit from the scanned batch")
    for label, x, stage in (("C01", 123, "green"), ("C02", 223, "turning")):
        fruit(f"assay-sample-{label}", p, x, 142, .78, stage)
        tag(p, f"assay-tag-{label}", x, 196, label)
    arrow(p, "assay-samples", 173, 208, 173, 230)
    rect(p, "assays-card", 19, 236, 308, 103, WARM, "#e9d7b9", 8)
    assay_icon("assay-ssc", p, 43, 261, "#d5a252", "#9f7137")
    assay_icon("assay-ta", p, 43, 309, "#a7b76e", "#687c49")
    text(p, "soluble-solids", 66, 269, "Soluble solids (SSC)", 17)
    path(p, "assay-divider", "M32 287 H314", stroke="#e6d4b8")
    text(p, "titratable-acidity", 66, 317, "Titratable acidity (TA)", 17)
    arrow(p, "to-join", 173, 346, 173, 369)
    text(p, "join-heading", 173, 394, "Match records by fruit ID", 20, INK, "middle")
    rect(p, "join-table", 19, 408, 308, 116, "#f7faf3", LINE, 8)
    text(p, "header-spectrum", 91, 432, "Spectrum", 15, MUTED, "middle")
    text(p, "header-reference", 255, 432, "Reference", 15, MUTED, "middle")
    for i, label in enumerate(("C01", "C02")):
        y = 460 + i * 36
        path(p, f"join-row-rule-{label}", f"M33 {y - 16} H313", stroke=LINE)
        path(p, f"join-link-{label}", f"M117 {y - 2} H229", stroke=GREEN, width=1.2)
        for end, x in (("spectrum", 91), ("reference", 255)):
            tag(p, f"join-{end}-{label}", x, y, label)


def modeling():
    p = panel("model", 1206, "04", "Calibrate & predict", "Keep each fruit in one set")
    text(p, "split-heading", 173, 111, "Split matched records by fruit", 17, INK, "middle")
    path(p, "split-branches", "M173 121 V133 H92 M173 133 H254", stroke=GREEN, width=1.5)
    for key, x, main, sub in (("calibration", 19, "Calibration", "Fit the model"),
                              ("validation", 181, "Independent validation", "Check the fitted model")):
        center = x + 73
        arrow(p, key + "-input", center, 133, center, 149)
        rect(p, key + "-card", x, 150, 146, 63, PALE, LINE, 8)
        text(p, key + "-name", center, 176, main, 13.5, INK, "middle")
        text(p, key + "-task", center, 197, sub, 13, MUTED, "middle")
    arrow(p, "calibration-to-model", 92, 215, 92, 238)
    rect(p, "fit-card", 19, 242, 146, 49, CARD, LINE, 8)
    text(p, "fit-label", 92, 272, "Fitted model", 16, GREEN, "middle")
    path(p, "held-out-check-link", "M165 267 H254", stroke=GREEN, width=1.35, stroke_dasharray=[4, 4])
    arrow(p, "held-out-check", 254, 267, 254, 216)
    rect(p, "new-scan-card", 181, 287, 146, 43, WARM, "#e9d7b9", 8)
    fruit("prediction-new-fruit", p, 204, 309, .30, "orange", 11)
    text(p, "new-scan-label", 224, 314, "New fruit scan", 14)
    path(p, "prediction-inputs", "M92 293 V342 H254 V332", stroke=GREEN, width=1.5)
    arrow(p, "prediction-input", 173, 342, 173, 360)
    rect(p, "predict-card", 19, 361, 308, 43, GREEN, r=8)
    text(p, "predict-label", 173, 388, "Predict SSC and TA", 18, "#ffffff", "middle")
    path(p, "output-branches", "M173 405 V419 H92 M173 419 H254", stroke=GREEN, width=1.5)
    for x, key, name, sub in ((19, "single", "Individual fruit", "One quality estimate"),
                               (181, "batch", "Batch assessment", "Summary across fruit")):
        center = x + 73
        arrow(p, key + "-output", center, 419, center, 437)
        rect(p, key + "-card", x, 439, 146, 96, WARM, "#e9d7b9", 8)
        text(p, key + "-title", center, 507, name, 15, INK, "middle")
        text(p, key + "-detail", center, 525, sub, 12.5, MUTED, "middle")
    fruit("output-single-fruit", p, 92, 469, .39, "orange", 12)
    for i, (x, y, stage) in enumerate(((242, 470, "turning"), (267, 470, "orange"), (254, 465, "orange"))):
        fruit(f"output-batch-fruit-{i}", p, x, y, .28, stage, 8)


def main():
    header = group("heading")
    text(header, "title", 48, 76, "Non-destructive prediction of citrus quality", 45)
    text(header, "subtitle", 50, 110, "From orchard sampling to fruit-level predictions and batch assessment", 21, MUTED)
    path(header, "accent", "M49 37 H110", stroke=GOLD, width=3)
    orchard()
    scanning()
    reference()
    modeling()
    links = group("workflow-links")
    for i, x in enumerate((398, 784, 1170), start=1):
        arrow(links, f"stage-{i}", x, 182, x + 31, 182)
    builder = SceneBuilder(1600, 746, background=PAPER, schema_version="0.3")
    builder.add_many(objects, resources=resources)
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", default="figure", help="New project directory name beside this script")
    write_project(builder.build(), Path(__file__).resolve().parent / parser.parse_args().project_name, scale=2)


if __name__ == "__main__":
    main()

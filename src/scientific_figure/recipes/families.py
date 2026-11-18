"""Original, bounded geometric constructions for the static recipe catalog."""
from __future__ import annotations

import math
import random

from .base import ellipse_path, line_metrics, poly


def require(condition, message):
    if not condition:
        raise ValueError(message)


def cardinal(f, rx, ry, cx=0, cy=0):
    f.ports.update(left=[cx-rx, cy], right=[cx+rx, cy], top=[cx, cy-ry],
                   bottom=[cx, cy+ry], center=[cx, cy])


def arrow(f, p):
    start, end = p["start"], p["end"]
    dx, dy = end[0]-start[0], end[1]-start[1]
    length = math.hypot(dx, dy)
    h, w, s = p["head_length"], p["head_width"], p["shaft_width"]
    require(length > h*(2 if p["double_head"] else 1), "arrow heads must fit between distinct endpoints")
    require(w >= s, "head_width must be >= shaft_width")
    ux, uy = dx/length, dy/length
    def at(distance, offset):
        return [start[0]+distance*ux-offset*uy, start[1]+distance*uy+offset*ux]
    tail = h if p["double_head"] else 0
    f.path("shaft", poly([at(tail, -s/2), at(length-h, -s/2), at(length-h, s/2), at(tail, s/2)], True))
    f.path("head", poly([at(length-h, -w/2), end, at(length-h, w/2)], True))
    if p["double_head"]:
        f.path("tail", poly([at(h, w/2), start, at(h, -w/2)], True))
    f.ports.update(start=list(start), end=list(end), center=at(length/2, 0))
    f.depend("head", ["shaft"], "construction_alignment")


def flow_node(f, p):
    box, tw, th = line_metrics(p["label"], p["font_size"])
    cw, ch = tw+2*p["padding"], th+2*p["padding"]
    factor = {"rounded_rect": 1, "ellipse": math.sqrt(2), "diamond": 2}[p["shape"]]
    w, h = max(p["width"], cw*factor), max(p["height"], ch*factor)
    require(max(w, h) <= 4096, "measured flow node exceeds 4096 local units; split the label explicitly")
    if p["shape"] == "ellipse":
        f.ellipse("body", w/2, h/2, w/2, h/2, "#e6edf3")
    elif p["shape"] == "diamond":
        f.path("body", poly([[w/2, 0], [w, h/2], [w/2, h], [0, h/2]], True), "#e6edf3")
    else:
        f.rect("body", 0, 0, w, h, radius=min(min(h, w)*.12, p["padding"]))
    f.text("label", (w-tw)/2-box[0], (h-th)/2-box[1], p["label"], p["font_size"])
    cardinal(f, w/2, h/2, w/2, h/2)
    f.depend("body", ["label"], "measured_text_fit")


def legend(f, p):
    x = y = 0
    size, font, gap = p["swatch_size"], p["font_size"], p["gap"]
    for i, item in enumerate(p["entries"]):
        box, tw, th = line_metrics(item["label"], font)
        height = max(size, th, font)
        if p["swatch_shape"] == "circle":
            f.ellipse(f"swatch.{i}", x+size/2, y+height/2, size/2, size/2, item["color"])
        else:
            f.rect(f"swatch.{i}", x, y+(height-size)/2, size, size, item["color"])
        f.text(f"label.{i}", x+size+gap-box[0], y+(height-th)/2-box[1], item["label"], font)
        f.depend(f"swatch.{i}", [f"label.{i}"], "measured_row_alignment")
        if p["orientation"] == "horizontal":
            x += size+gap+tw+gap
        else:
            y += height+gap
    require(max(x, y) <= 4096, "legend extent exceeds 4096; use multiple legends")
    f.ports.update(start=[0, 0], end=[x-gap if x else 0, y-gap if y else 0])


def zoom_callout(f, p):
    x, y, w, h = p["roi"]
    ix, iy = p["inset_origin"]
    iw, ih = w*p["magnification"], h*p["magnification"]
    require(w > 0 and h > 0 and max(iw, ih) <= 4096, "ROI must have positive extents and bounded inset")
    require(ix >= x+w or ix+iw <= x or iy >= y+h or iy+ih <= y, "ROI and inset frames must be disjoint")
    stroke = "#6286a6"
    f.add("roi", "rect", dict(x=x, y=y, width=w, height=h), style={"fill": "none", "stroke": stroke, "stroke_width": 1.5})
    f.add("inset", "rect", dict(x=ix, y=iy, width=iw, height=ih), style={"fill": "none", "stroke": stroke, "stroke_width": 1.5})
    if ix >= x+w:
        pairs = [([x+w, y], [ix, iy]), ([x+w, y+h], [ix, iy+ih])]
    elif ix+iw <= x:
        pairs = [([x, y], [ix+iw, iy]), ([x, y+h], [ix+iw, iy+ih])]
    elif iy >= y+h:
        pairs = [([x, y+h], [ix, iy]), ([x+w, y+h], [ix+iw, iy])]
    else:
        pairs = [([x, y], [ix, iy+ih]), ([x+w, y], [ix+iw, iy+ih])]
    for i, ends in enumerate(pairs):
        f.path(f"link.{i}", poly(ends), "none", stroke=stroke)
        f.depend(f"link.{i}", ["roi", "inset"], "frame_correspondence")
    if p["show_label"]:
        f.text("label", ix, iy+ih+p["font_size"]*1.5, f"{p['magnification']:g}x", p["font_size"])
    f.ports.update(source=[x+w/2, y+h/2], inset=[ix+iw/2, iy+ih/2])


def leaf(f, p):
    length, width, bend = p["length"], p["width"], p["bend"]
    require(width <= length and abs(bend) <= length/2, "leaf width or bend exceeds length constraint")
    require(4*abs(bend)*width < length*length, "leaf width and curvature would fold the blade contour")
    def center(t):
        return [length*t, bend*4*t*(1-t)]
    def side(t, sign):
        x, y = center(t)
        slope = bend*4*(1-2*t)/length
        half = width/2*max(0, math.sin(math.pi*t))**p["tip_taper"]
        scale = half/math.hypot(1, slope)
        return [x-sign*slope*scale, y+sign*scale]
    ts = [i/48 for i in range(49)]
    f.path("blade", poly([side(t, 1) for t in ts] + [side(t, -1) for t in reversed(ts)], True), "#7ca887")
    f.path("midrib", poly([center(t) for t in ts]), "none", stroke="#42694c", width=1.5)
    if p["petiole_length"]:
        f.path("petiole", poly([[-p["petiole_length"], -bend*4*p["petiole_length"]/length], [0, 0]]),
               "none", stroke="#42694c", width=2)
    for i in range(p["vein_count"]):
        t = (i+1)/(p["vein_count"]+1)*.85
        for sign, name in ((-1, "left"), (1, "right")):
            key = f"vein.{i}.{name}"
            f.path(key, poly([center(max(0, t-.12)), side(t, sign)]), "none", stroke="#567f5f", width=.8)
            f.depend(key, ["blade", "midrib"])
    f.ports.update(base=[0, 0], tip=[length, 0], stem=[-p["petiole_length"], -bend*4*p["petiole_length"]/length])
    f.depend("midrib", ["blade"])


def cell_symbol(f, p):
    rx, ry, thick = p["rx"], p["ry"], p["membrane_thickness"]
    require(thick < min(rx, ry), "membrane thickness must be smaller than both cell radii")
    f.ellipse("membrane", 0, 0, rx, ry, "#779b91")
    f.ellipse("cytoplasm", 0, 0, rx-thick, ry-thick, "#d9e9df")
    if p["nucleus"]:
        nx, ny = p["nucleus_offset"]
        nr = p["nucleus_radius"]
        # Triangle inequality in normalized ellipse coordinates gives a safe,
        # conservative containment bound for the complete nuclear disk.
        require(math.hypot(nx/(rx-thick), ny/(ry-thick))+nr/min(rx-thick, ry-thick) < 1,
                "enabled nucleus must fit inside the cytoplasm")
        f.ellipse("nucleus", nx, ny, nr, nr, "#9a8db4")
        f.depend("nucleus", ["cytoplasm"], "containment")
    cardinal(f, rx, ry)
    f.depend("cytoplasm", ["membrane"])


def check_line(points):
    require(all(math.dist(a, b) > 1e-6 for a, b in zip(points, points[1:])), "consecutive centerline points must differ")


def vessel_segment(f, p):
    from ..pathops_backend import stroke_outline
    line = p["centerline"]
    check_line(line)
    lines = [("", line)]
    for i, branch in enumerate(p["branches"]):
        check_line(branch)
        require(branch[0] in line, "branch start must equal a main centerline vertex")
        lines.append((f"branch.{i}.", branch))
        f.ports[f"branch.{i}"] = list(branch[-1])
    for prefix, points in lines:
        f.path(prefix+"wall", stroke_outline(poly(points), p["lumen_width"]+2*p["wall_thickness"]), "#b87878")
    for prefix, points in lines:
        f.path(prefix+"lumen", stroke_outline(poly(points), p["lumen_width"]), "#f2dcdc")
        f.depend(prefix+"lumen", [prefix+"wall"])
    if p["cut_faces"]:
        require(line[0][1] == line[1][1] and line[-1][1] == line[-2][1],
                "cut faces require horizontal endpoint tangents")
        for name, xy in (("start", line[0]), ("end", line[-1])):
            rx, ry = p["wall_thickness"]/2, p["lumen_width"]/2
            f.path("cut."+name, ellipse_path(*xy, rx, ry+p["wall_thickness"])+" "+
                   ellipse_path(*xy, rx*.65, ry), "#b87878")
            f.objects[-1]["style"]["fill_rule"] = "evenodd"
            f.depend("cut."+name, ["wall", "lumen"], "cut_face")
    f.ports.update(start=list(line[0]), end=list(line[-1]))


def particle(f, p):
    r, thick, pr, count = p["radius"], p["shell_thickness"], p["payload_radius"], p["payload_count"]
    require(thick < r, "shell_thickness must be smaller than radius")
    available = r-thick-pr
    require(not count or available >= 0, "payload symbols do not fit in the core")
    rng = random.Random(p["seed"])
    phase = rng.random()*2*math.pi
    positions = [[0., 0.]] if count == 1 else [
        [available*math.sqrt((i+.5)/count)*math.cos(phase+i*math.pi*(3-math.sqrt(5))),
         available*math.sqrt((i+.5)/count)*math.sin(phase+i*math.pi*(3-math.sqrt(5)))] for i in range(count)]
    require(all(math.dist(a, b) >= 2*pr for i, a in enumerate(positions) for b in positions[i+1:]),
            "payload symbols overlap; reduce count/radius or enlarge the particle")
    paint = f.gradient("shell_shade", ["#a9c6d9", "#547d9a"]) if p["shading"] else "#7299b2"
    f.ellipse("shell", 0, 0, r, r, paint)
    f.ellipse("core", 0, 0, r-thick, r-thick, "#e4edf3")
    for i, (x, y) in enumerate(positions):
        f.ellipse(f"payload.{i}", x, y, pr, pr, "#bc8555")
        f.depend(f"payload.{i}", ["core"], "containment")
    cardinal(f, r, r)
    f.depend("core", ["shell"])


def loop_band(f, p):
    rx, ry, width = p["rx"], p["ry"], p["band_width"]
    require(width < min(rx, ry), "band width must leave a positive elliptical hole")
    require(p["loop_count"] == 1 or p["spacing"] > width, "loop spacing must exceed band width")
    for i in range(p["loop_count"]):
        x, y = rx+i*p["spacing"], ry+i*p["spacing"]
        key = f"loop.{i}"
        f.path(key, ellipse_path(0, 0, x, y)+" "+ellipse_path(0, 0, x-width, y-width), "#7299b2")
        f.objects[-1]["style"]["fill_rule"] = "evenodd"
    offset = (p["loop_count"]-1)*p["spacing"]
    cardinal(f, rx+offset, ry+offset)


def interwoven_bands(f, p):
    for i, band in enumerate(p["bands"]):
        check_line(band["points"])
        f.path(f"band.{i}", poly(band["points"]), "none", stroke=band["color"], width=band["width"])
        f.ports[f"band.{i}.start"] = list(band["points"][0])
        f.ports[f"band.{i}.end"] = list(band["points"][-1])
    for i, cross in enumerate(p["crossings"]):
        a, b = cross["over"], cross["under"]
        require(a != b and max(a, b) < len(p["bands"]), "crossing must reference two distinct existing bands")
        require(min(cross["region"][2:]) > 0, "crossing region extents must be positive")
        relation = {"id": f.oid(f"crossing.{i}"), "over": f.parts[f"band.{a}"], "under": f.parts[f"band.{b}"],
                    "frame": f.parts[cross["frame"]], "region": list(cross["region"]), "gap": cross["gap"]}
        f.occlusions.append(relation)
        f.depend(f"band.{b}", [f"band.{a}", "root"], "explicit_occlusion")


def fruit(f, p):
    length, width, bend = p["length"], p["width"], p["bend"]
    require(width <= 2*length and abs(bend) <= width/2, "fruit width/bend outside cross-parameter range")
    require(p["cutaway"] or not p["seed_count"], "seeds require an explicit cutaway")
    contour = []
    for i in range(64):
        t = 2*math.pi*i/64
        upper = (1+math.cos(t))/2
        contour.append([width/2*math.sin(t)*(1-(1-p["neck_ratio"])*upper)+bend*upper, -length/2*math.cos(t)])
    f.path("body", poly(contour, True), "#c99b58")
    if p["stem_length"]:
        f.path("stem", poly([[bend, -length/2], [bend+p["stem_length"]*.2, -length/2-p["stem_length"]]]),
               "none", stroke="#766143", width=3)
    if p["cutaway"]:
        f.path("cutaway", poly([[x*.82, y*.82] for x, y in contour], True), "#f2dfb7")
        f.depend("cutaway", ["body"])
        for i in range(p["seed_count"]):
            angle = i*2*math.pi/p["seed_count"]
            f.ellipse(f"seed.{i}", width*.13*math.cos(angle), length*.16*math.sin(angle), width*.025, length*.035, "#786044")
            f.depend(f"seed.{i}", ["cutaway"])
    f.ports.update(stem=[bend+p["stem_length"]*.2, -length/2-p["stem_length"]], base=[0, length/2], center=[0, 0])


def simple_microbe(f, p):
    length, width, bend = p["length"], p["width"], p["curvature"]
    require(width <= length and abs(bend) <= length/3, "microbe width/curvature outside supported proportions")
    contour = []
    for i in range(64):
        t = 2*math.pi*i/64
        x = length/2*(1+math.cos(t))
        contour.append([x, width/2*math.sin(t)+bend*4*(x/length)*(1-x/length)])
    # Paint appendages first so attachments meet the body without exposed joins.
    for i in range(p["flagella_count"]):
        angle = 2*math.pi*i/p["flagella_count"]
        x = length/2*(1+math.cos(angle))
        y = width/2*math.sin(angle)+bend*4*(x/length)*(1-x/length)
        points = []
        for step in range(25):
            t = step/24
            distance = p["flagella_length"]*t
            wave = math.sin(t*3*math.pi)*width*.1
            points.append([x+distance*math.cos(angle)-wave*math.sin(angle),
                           y+distance*math.sin(angle)+wave*math.cos(angle)])
        f.path(f"flagellum.{i}", poly(points), "none", stroke="#668e8d", width=1.5)
    f.path("body", poly(contour, True), "#8bb5ae")
    if p["nucleoid"]:
        points = [[length*(.3+.4*i/24), bend*4*(.3+.4*i/24)*(1-(.3+.4*i/24))+width*.12*math.sin(i*math.pi/4)] for i in range(25)]
        f.path("nucleoid", poly(points), "none", stroke="#4c6c80", width=2)
        f.depend("nucleoid", ["body"])
    for i in range(p["flagella_count"]):
        f.depend(f"flagellum.{i}", ["body"], "attachment")
    f.ports.update(start=[0, 0], end=[length, 0], center=[length/2, bend])


def lab_lamp(f, p):
    def step(start, length, degrees):
        angle = math.radians(degrees)
        return [start[0]+length*math.cos(angle), start[1]+length*math.sin(angle)]
    base = [0, 0]
    elbow = step(base, p["lower_length"], p["lower_angle"])
    light = step(elbow, p["upper_length"], p["upper_angle"])
    angle = math.radians(p["light_direction"])
    u, n = [math.cos(angle), math.sin(angle)], [-math.sin(angle), math.cos(angle)]
    def at(distance, offset):
        return [light[0]+u[0]*distance+n[0]*offset, light[1]+u[1]*distance+n[1]*offset]
    w, depth = p["shade_width"], p["shade_depth"]
    if p["beam"]:
        f.path("beam", poly([at(depth, -w/2), at(depth+90, -w*1.5), at(depth+90, w*1.5), at(depth, w/2)], True), "#f3e8b6")
        f.objects[-1]["style"]["fill_opacity"] = .45
    f.rect("base", -p["base_width"]/2, 0, p["base_width"], p["base_height"], "#697d8c", radius=2)
    f.path("arm.lower", poly([base, elbow]), "none", stroke="#697d8c", width=6)
    f.path("arm.upper", poly([elbow, light]), "none", stroke="#697d8c", width=5)
    f.ellipse("joint.base", *base, 5, 5, "#4e6372")
    f.ellipse("joint.elbow", *elbow, 5, 5, "#4e6372")
    f.path("shade", poly([at(0, -w*.18), at(depth, -w/2), at(depth, w/2), at(0, w*.18)], True), "#9cafba")
    f.ports.update(base=base, elbow=elbow, light=at(depth, 0))
    f.depend("shade", ["arm.upper"], "attachment")
    f.depend("arm.upper", ["arm.lower"], "joint")
    if p["beam"]:
        f.depend("beam", ["shade"], "light_direction")


def lab_book(f, p):
    w, h, fold, thick, margin = p["page_width"], p["page_height"], p["fold"], p["page_thickness"], p["cover_margin"]
    require(fold < h/2 and thick < h/3, "book fold or page thickness exceeds page proportions")
    left = [[-w, 0], [0, fold], [0, h+fold], [-w, h]]
    right = [[0, fold], [w, 0], [w, h], [0, h+fold]]
    for name, points in (("left", left), ("right", right)):
        sign = -1 if name == "left" else 1
        cover = [[x+(sign*margin if x else 0), y+thick+margin] for x, y in points]
        f.path("cover."+name, poly(cover, True), "#6286a6")
    for name, points in (("left", left), ("right", right)):
        f.path("pages."+name, poly([[x, y+thick] for x, y in points], True), "#d2d6d4")
        # The page face is a nested independently editable part of the page stack.
        f.path("face."+name, poly(points, True), "#f3f0e7")
        f.depend("face."+name, ["pages."+name])
        for i in range(p["line_count"]):
            t = (i+1)/(p["line_count"]+1)
            if name == "left":
                ends = [[-w*.82, h*t+fold*.18], [-w*.18, h*t+fold*.82]]
            else:
                ends = [[w*.18, h*t+fold*.82], [w*.82, h*t+fold*.18]]
            f.path(f"line.{name}.{i}", poly(ends), "none", stroke="#b3b9b7", width=1)
            f.depend(f"line.{name}.{i}", ["face."+name])
    f.path("fold", poly([[0, fold], [0, h+fold+thick]]), "none", stroke="#919fa5", width=1.5)
    f.ports.update(spine=[0, h/2+fold], left=[-w, h/2], right=[w, h/2])


def cluster(f, p):
    count, radius, gap, jitter = p["count"], p["radius"], p["gap"], p["jitter"]
    require(jitter <= gap/2, "cluster jitter must be <= gap/2 to preserve non-overlap")
    rng = random.Random(p["seed"])
    step = 2*radius+gap
    if p["layout"] == "ring" and count > 1:
        ring = step/(2*math.sin(math.pi/count))
        phase = rng.random()*2*math.pi
        positions = [[ring*math.cos(phase+i*2*math.pi/count), ring*math.sin(phase+i*2*math.pi/count)] for i in range(count)]
    else:
        columns = min(count, p["columns"])
        rows = math.ceil(count/columns)
        positions = [[(i % columns-(columns-1)/2)*step, (i//columns-(rows-1)/2)*step] for i in range(count)]
    for i, (x, y) in enumerate(positions):
        angle, distance = rng.random()*2*math.pi, rng.random()*jitter
        xy = [x+distance*math.cos(angle), y+distance*math.sin(angle)]
        f.ellipse(f"member.{i}", *xy, radius, radius, "#7299b2")
        f.ports[f"member.{i}"] = xy
    f.ports["center"] = [0, 0]


BUILDERS = {
    "arrow": arrow, "flow_node": flow_node, "legend": legend, "zoom_callout": zoom_callout,
    "leaf": leaf, "cell_symbol": cell_symbol, "vessel_segment": vessel_segment, "particle": particle,
    "loop_band": loop_band, "interwoven_bands": interwoven_bands, "fruit": fruit,
    "simple_microbe": simple_microbe, "lab_lamp": lab_lamp, "lab_book": lab_book, "cluster": cluster,
}

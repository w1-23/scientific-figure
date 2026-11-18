"""Static, trusted recipe contracts. No module names or callables in input JSON."""
from copy import deepcopy

from .base import SOURCE, VERSION


def num(default, low, high):
    return {"type": "number", "minimum": low, "maximum": high, "default": default}


def integer(default, low, high):
    return {"type": "integer", "minimum": low, "maximum": high, "default": default}


def boolean(default):
    return {"type": "boolean", "default": default}


def choice(default, values):
    return {"type": "string", "enum": values, "default": default}


def text(default, limit=1000):
    return {"type": "string", "maxLength": limit, "default": default}


def array(items, low, high, default=None):
    spec = {"type": "array", "items": items, "minItems": low, "maxItems": high}
    if default is not None:
        spec["default"] = default
    return spec


def obj(properties, required=None):
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties) if required is None else required}


COORD = {"type": "number", "minimum": -8192, "maximum": 8192}
XY = array(COORD, 2, 2)
POINTS = array(XY, 2, 32)
PAINT = {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"}


def xy(default):
    return {**deepcopy(XY), "default": default}


def entry(summary, properties, *, constraints=(), parts=(), ports=(), examples=(),
          limitations=(), required=(), backend=False, scene="0.1"):
    return {"id": "", "kind": "recipe", "version": VERSION, "status": "supported",
            "summary": summary, "params_schema": obj(properties, required),
            "defaults": {k: deepcopy(v["default"]) for k, v in properties.items() if "default" in v},
            "cross_parameter_constraints": list(constraints), "part_keys": list(parts),
            "ports": list(ports), "local_coordinates": "Root-local Cartesian SVG coordinates; +y down. Instance affine is applied to the root only.",
            "source": deepcopy(SOURCE), "capabilities": {"minimum_scene_version": scene,
                "backend": ["fontTools", "skia-pathops"] if backend else ["native_scene"],
                "persistent_ports_scene_version": "0.3"},
            "resource_scope": "Instance-private deterministic IDs; no mutable resources shared between instances.",
            "determinism": "Fixed 1.0.0 geometry; seed is explicit in normalized parameters for randomized families.",
            "output_budget": {"objects": 1000, "resources": 32, "occlusions": 32,
                              "aggregate": "Scene limits still apply to the final atomic batch."},
            "style_bindings": {"syntax": "existing_part_key.fill or existing_part_key.stroke",
                               "values": "Concrete Scene hex colors or none; tokens must be resolved explicitly before build."},
            "dependencies": "Result reports construction-time geometry dependencies; these are input provenance, not live constraints.",
            "suitable_for": [summary], "unsuitable_for": ["Inferring unknown scientific structure from a reference image"],
            "limitations": ["Schematic geometry; scientific review is the caller's responsibility.",
                            "No automatic template upgrades or reverse recovery of params from edited paths.",
                            "Old Scene versions return local helper ports without persisting anchors.", *limitations],
            "examples": list(examples),
            "preview": {"status": "not_bundled", "method": "Build an example, then use the existing Scene SVG/PNG exporter."}}


CATALOG = {
    "arrow": entry("Explicit directed arrow with independently editable shaft and heads", {
        "start": xy([0, 0]), "end": xy([100, 0]), "shaft_width": num(5, .2, 200),
        "head_length": num(18, 1, 500), "head_width": num(18, 1, 500), "double_head": boolean(False)},
        constraints=["Endpoints differ; head_width >= shaft_width; total head lengths < endpoint distance."],
        parts=["root", "shaft", "head", "tail (optional)"], ports=["start", "end", "center"],
        examples=[{"end": [140, 40], "head_width": 24}, {"end": [0, 160], "double_head": True, "shaft_width": 8}]),
    "flow_node": entry("Measured flow node or card with explicit shape and label", {
        "width": num(120, 1, 2000), "height": num(60, 1, 2000), "padding": num(12, 0, 200),
        "font_size": num(14, 4, 100), "label": text("Process"),
        "shape": choice("rounded_rect", ["rounded_rect", "ellipse", "diamond"])},
        parts=["root", "body", "label"], ports=["left", "right", "top", "bottom", "center"],
        limitations=["Width/height are minimum extents; measured text and padding can enlarge the node.",
                     "Newlines are explicit; text is never silently wrapped."],
        examples=[{"label": "Long process label\nwith two lines", "shape": "diamond"}, {"width": 180, "shape": "ellipse"}]),
    "legend": entry("Measured legend with independent swatches and labels", {
        "entries": array(obj({"label": text("", 300), "color": PAINT}), 1, 32,
                         [{"label": "Sample", "color": "#6286a6"}, {"label": "Control", "color": "#b48558"}]),
        "orientation": choice("vertical", ["vertical", "horizontal"]), "gap": num(12, 0, 200),
        "swatch_size": num(12, 1, 100), "font_size": num(14, 4, 100),
        "swatch_shape": choice("rect", ["rect", "circle"])},
        parts=["root", "swatch.N", "label.N"], ports=["start", "end"],
        examples=[{"orientation": "horizontal", "swatch_size": 20}, {"font_size": 20, "swatch_shape": "circle"}]),
    "zoom_callout": entry("Explicit ROI frame, linked magnification frame and scale label", {
        "roi": array(COORD, 4, 4, [0, 0, 40, 30]), "inset_origin": xy([120, 0]),
        "magnification": num(2, .1, 20), "show_label": boolean(True), "font_size": num(12, 4, 80)},
        constraints=["ROI extents are positive; inset and ROI must be disjoint; inset extents <=4096."],
        parts=["root", "roi", "inset", "link.0", "link.1", "label (optional)"],
        ports=["source", "inset"], limitations=["Frames do not invent magnified content; caller supplies corresponding content explicitly.",
            "Magnification states the inset-to-ROI size ratio in local coordinates; nonuniform instance scaling changes physical aspect."],
        examples=[{"magnification": 3, "inset_origin": [180, 40]}, {"roi": [10, 20, 30, 45], "magnification": 1.5}]),
    "leaf": entry("Curved leaf blade with explicit petiole, midrib and optional paired veins", {
        "length": num(120, 10, 1000), "width": num(50, 2, 500), "bend": num(12, -300, 300),
        "tip_taper": num(1, .4, 3), "petiole_length": num(18, 0, 300), "vein_count": integer(4, 0, 16)},
        constraints=["width <= length; abs(bend) <= length/2; 4*abs(bend)*width < length^2 prevents a folded blade."],
        parts=["root", "blade", "midrib", "petiole (optional)", "vein.N.left", "vein.N.right"], ports=["base", "tip", "stem"],
        limitations=["Sampled analytic blade contour; no botanical species identity or inferred venation."],
        examples=[{"bend": -25, "vein_count": 2}, {"length": 170, "width": 35, "tip_taper": 1.8}]),
    "cell_symbol": entry("Explicit schematic membrane, cytoplasm and optional nucleus", {
        "rx": num(50, 5, 500), "ry": num(36, 5, 500), "membrane_thickness": num(3, .2, 80),
        "nucleus": boolean(True), "nucleus_radius": num(12, 1, 200), "nucleus_offset": xy([0, 0])},
        constraints=["Membrane thickness < min(rx,ry); enabled nucleus fits completely within cytoplasm."],
        parts=["root", "membrane", "cytoplasm", "nucleus (optional)"], ports=["left", "right", "top", "bottom", "center"],
        limitations=["No unknown organelles, organism identity or biological scale are supplied."],
        examples=[{"rx": 70, "ry": 28, "nucleus": False}, {"membrane_thickness": 6, "nucleus_offset": [12, 0]}]),
    "vessel_segment": entry("Explicit centerline vessel schematic with separate wall and lumen", {
        "centerline": {**deepcopy(POINTS), "default": [[0, 0], [60, 12], [120, 0]]},
        "lumen_width": num(20, 1, 200), "wall_thickness": num(4, .2, 60),
        "branches": array(POINTS, 0, 4, []), "cut_faces": boolean(False)},
        constraints=["Consecutive centerline points differ; branch starts must match a main centerline vertex.",
                     "Cut faces require horizontal endpoint tangents."],
        parts=["root", "wall", "lumen", "branch.N.wall", "branch.N.lumen", "cut.start/end (optional)"],
        ports=["start", "end", "branch.N"], backend=True,
        limitations=["Constant local round stroke outlines, not anatomy or a fluid model.", "Branch unions are visual overlays; editable sources remain separate."],
        examples=[{"centerline": [[0, 0], [50, -20], [100, 30]], "lumen_width": 12},
                  {"wall_thickness": 7, "centerline": [[0, 0], [40, 0], [80, 20], [120, 20]],
                   "branches": [[[40, 0], [80, 70]]], "cut_faces": True}]),
    "particle": entry("Concentric particle shell with explicit seeded payload symbols", {
        "radius": num(38, 3, 500), "shell_thickness": num(5, .2, 100),
        "payload_count": integer(8, 0, 96), "payload_radius": num(3, .2, 80),
        "seed": integer(17, 0, 2147483647), "shading": boolean(False)},
        constraints=["shell_thickness < radius; payload disks fit and do not overlap in the deterministic layout."],
        parts=["root", "shell", "core", "payload.N"], ports=["left", "right", "top", "bottom", "center"],
        limitations=["Payload count and shell layers are caller-selected symbols, not inferred molecular quantities."],
        examples=[{"radius": 52, "shell_thickness": 9, "payload_count": 15}, {"payload_count": 4, "payload_radius": 5, "seed": 42}]),
    "loop_band": entry("One or more independent elliptical bands with explicit holes", {
        "rx": num(65, 5, 500), "ry": num(35, 5, 500), "band_width": num(8, .2, 100),
        "loop_count": integer(1, 1, 8), "spacing": num(15, .2, 200)},
        constraints=["band_width < min(rx,ry); multiple loops require spacing > band_width."],
        parts=["root", "loop.N"], ports=["left", "right", "top", "bottom", "center"],
        limitations=["Elliptical concentric bands only; no inferred knots or membrane topology."],
        examples=[{"rx": 85, "ry": 25, "band_width": 5}, {"loop_count": 3, "spacing": 18}]),
    "interwoven_bands": entry("Explicit band centerlines and caller-declared local over/under relations", {
        "bands": array(obj({"points": POINTS, "width": num(8, .2, 100), "color": PAINT}), 2, 8),
        "crossings": array(obj({"over": integer(0, 0, 7), "under": integer(1, 0, 7),
                                "frame": {"const": "root"}, "region": array(COORD, 4, 4),
                                "gap": num(1, 0, 1000)}), 1, 32)},
        required=["bands", "crossings"], backend=True, scene="0.2",
        constraints=["Every crossing specifies over, under, frame=root, positive region, and gap.",
                     "Indices reference distinct existing bands; geometric occlusion restrictions still apply."],
        parts=["root", "band.N"], ports=["band.N.start", "band.N.end"],
        limitations=["Never infers crossings or alternation. Existing occlusion evaluator reports missing or out-of-region intersections as needs_review."],
        examples=[{"bands": [{"points": [[0, 30], [100, 30]], "width": 8, "color": "#6286a6"},
                               {"points": [[50, 0], [50, 60]], "width": 8, "color": "#b48558"}],
                   "crossings": [{"over": 0, "under": 1, "frame": "root", "region": [40, 20, 20, 20], "gap": 1}]},
                  {"bands": [{"points": [[0, 30], [100, 30]], "width": 12, "color": "#6286a6"},
                             {"points": [[30, 0], [60, 60]], "width": 8, "color": "#b48558"}],
                   "crossings": [{"over": 1, "under": 0, "frame": "root", "region": [30, 18, 30, 24], "gap": 2}]}]),
    "fruit": entry("Parametric pear-like fruit with optional explicit cutaway and seeds", {
        "length": num(90, 10, 500), "width": num(65, 5, 400), "neck_ratio": num(.55, .2, 1),
        "bend": num(4, -100, 100), "stem_length": num(18, 0, 100),
        "cutaway": boolean(False), "seed_count": integer(0, 0, 12)},
        constraints=["width <= 2*length; abs(bend) <= width/2; seeds require explicit cutaway."],
        parts=["root", "body", "stem (optional)", "cutaway (optional)", "seed.N"], ports=["stem", "base", "center"],
        limitations=["Symbolic cutaway, not species-specific internal anatomy."],
        examples=[{"neck_ratio": .9, "width": 85}, {"cutaway": True, "seed_count": 4, "bend": -10}]),
    "simple_microbe": entry("Curved microbial symbol with explicitly enabled appendages and nucleoid", {
        "length": num(100, 10, 600), "width": num(32, 4, 200), "curvature": num(8, -150, 150),
        "flagella_count": integer(0, 0, 12), "flagella_length": num(30, 2, 150), "nucleoid": boolean(False)},
        constraints=["width <= length; abs(curvature) <= length/3."],
        parts=["root", "body", "flagellum.N", "nucleoid (optional)"], ports=["start", "end", "center"],
        limitations=["Appendages and nucleoid appear only when explicitly selected; no pathogen identity is implied."],
        examples=[{"curvature": -15, "flagella_count": 3}, {"length": 60, "width": 40, "nucleoid": True}]),
    "lab_lamp": entry("Articulated laboratory lamp with independent joints, shade and optional beam", {
        "base_width": num(70, 10, 300), "base_height": num(10, 2, 60),
        "lower_length": num(80, 10, 300), "upper_length": num(65, 10, 300),
        "lower_angle": num(-70, -170, -10), "upper_angle": num(-25, -170, 90),
        "shade_width": num(40, 5, 150), "shade_depth": num(28, 5, 150), "beam": boolean(False),
        "light_direction": num(70, -180, 180)},
        parts=["root", "base", "arm.lower", "arm.upper", "joint.base", "joint.elbow", "shade", "beam (optional)"],
        ports=["base", "elbow", "light"], limitations=["Flat mechanism symbol; angles are absolute local degrees, not a mechanical constraint solver."],
        examples=[{"lower_angle": -110, "upper_angle": -15}, {"shade_width": 55, "upper_length": 95, "beam": True}]),
    "lab_book": entry("Open book with independently editable covers, pages and fold", {
        "page_width": num(65, 10, 300), "page_height": num(90, 10, 400),
        "page_thickness": num(6, .2, 30), "fold": num(12, 0, 80),
        "line_count": integer(4, 0, 16), "cover_margin": num(4, .2, 20)},
        constraints=["fold < page_height/2; page_thickness < page_height/3."],
        parts=["root", "cover.left", "cover.right", "pages.left", "pages.right", "face.left", "face.right", "fold", "line.left.N", "line.right.N"],
        ports=["spine", "left", "right"], limitations=["Flat schematic folds; no page simulation or automatic scientific text."],
        examples=[{"page_width": 90, "page_thickness": 10}, {"fold": 25, "line_count": 8}]),
    "cluster": entry("Seeded independent circular members in an explicit grid or ring layout", {
        "count": integer(7, 1, 31), "radius": num(10, .5, 80), "gap": num(4, 0, 100),
        "layout": choice("grid", ["grid", "ring"]), "columns": integer(3, 1, 16),
        "jitter": num(0, 0, 20), "seed": integer(17, 0, 2147483647)},
        constraints=["jitter <= gap/2, maintaining disjoint members."],
        parts=["root", "member.N"], ports=["center", "member.N"],
        limitations=["Member count/layout are schematic inputs, not inferred particle counts or packing physics."],
        examples=[{"count": 12, "columns": 4, "gap": 8, "jitter": 2}, {"count": 6, "layout": "ring", "radius": 14}]),
}

for _key, _value in CATALOG.items():
    _value["id"] = _key
    _value["tier"] = "P3a" if _key in {"arrow", "flow_node", "legend", "zoom_callout", "leaf", "cell_symbol", "vessel_segment", "particle"} else "P3b"
    if _key in {"flow_node", "legend", "zoom_callout"}:
        _value["capabilities"]["font_metrics"] = "Existing selected local font; no bundled font files."
        _value["determinism"] += " Text also depends on the selected local font, reported by family and SHA256 in font_dependencies."

"""File-based CLI for agents; stdout is a single JSON receipt."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from PIL import Image

from . import __version__
from .editing import apply_patch, export_scene
from .project import PROJECT_JSON_EXTRAS, digest, json_text, load_project, write_project
from .reconstruct import reconstruct
from .render import render_svg, runtime_info
from .scene import load_json, manifest, validate
from .svg import to_svg


class Parser(argparse.ArgumentParser):
    def error(self,message):raise ValueError("arguments: "+message)


def parser():
    p=Parser(prog="figure-cli",description="Agent-assisted editable SVG reconstruction prototype")
    p.add_argument("--version",action="version",version=__version__)
    sub=p.add_subparsers(dest="command",required=True)
    doctor=sub.add_parser("doctor");doctor.add_argument("--json",action="store_true")
    evidence=sub.add_parser("evidence");evidence.add_argument("input",type=Path);evidence.add_argument("request",type=Path,nargs="?")
    evidence.add_argument("--output",type=Path)
    evidence.add_argument("--channels",default="contrast");evidence.add_argument("--roi")
    evidence.add_argument("--background");evidence.add_argument("--validate",action="store_true")
    prepare=sub.add_parser("prepare");prepare.add_argument("input",type=Path);prepare.add_argument("--output",type=Path,required=True)
    consume=sub.add_parser("consume");consume.add_argument("input",type=Path);consume.add_argument("--ids");consume.add_argument("--views",default="source.png");consume.add_argument("--output",type=Path)
    registry=sub.add_parser("registry");registry.add_argument("action",choices=["list","describe","search"]);registry.add_argument("id",nargs="?");registry.add_argument("--registry",type=Path);registry.add_argument("--version")
    recipe=sub.add_parser("recipe");recipe.add_argument("action",choices=["list","describe","build"]);recipe.add_argument("id",nargs="?");recipe.add_argument("--params",type=Path);recipe.add_argument("--instance",default="instance");recipe.add_argument("--output",type=Path);recipe.add_argument("--width",type=float,default=512);recipe.add_argument("--height",type=float,default=512);recipe.add_argument("--version",default="1.0.0");recipe.add_argument("--scale",type=float,default=3)
    plan=sub.add_parser("plan");plan.add_argument("action",choices=["draft","check","suggest","validate","compile","review"]);plan.add_argument("input",type=Path);plan.add_argument("--fragments",type=Path);plan.add_argument("--output",type=Path);plan.add_argument("--candidate",action="store_true");plan.add_argument("--scale",type=float,default=3)
    plan.add_argument("--context",type=Path);plan.add_argument("--phase",choices=["semantic","construction"],default="semantic");plan.add_argument("--reference",type=Path)
    template=sub.add_parser("template");template.add_argument("action",choices=["candidate"]);template.add_argument("input",type=Path);template.add_argument("--updates",type=Path,required=True);template.add_argument("--output",type=Path,required=True)
    text=sub.add_parser("text");text.add_argument("action",choices=["measure","wrap","validate-inventory"]);text.add_argument("input",type=Path)
    candidates=sub.add_parser("candidates");candidates.add_argument("input",type=Path);candidates.add_argument("--source",type=Path,required=True);candidates.add_argument("--roi");candidates.add_argument("--background")
    style=sub.add_parser("style-check");style.add_argument("input",type=Path)
    context=sub.add_parser("context");context_sub=context.add_subparsers(dest="action",required=True)
    attach=context_sub.add_parser("attach");attach.add_argument("input",type=Path)
    attach.add_argument("--context",type=Path,required=True);attach.add_argument("--base-revision",type=int,required=True)
    attach.add_argument("--output",type=Path)
    generation=sub.add_parser("generation");generation_sub=generation.add_subparsers(dest="action",required=True)
    diff=generation_sub.add_parser("diff");diff.add_argument("input",type=Path)
    diff.add_argument("--candidate",type=Path,required=True);diff.add_argument("--output",type=Path,required=True)
    accept=generation_sub.add_parser("accept");accept.add_argument("input",type=Path)
    accept.add_argument("--candidate",type=Path,required=True);accept.add_argument("--base-revision",type=int,required=True)
    bundle=sub.add_parser("bundle");bundle_sub=bundle.add_subparsers(dest="action",required=True)
    create=bundle_sub.add_parser("create");create.add_argument("input",type=Path);create.add_argument("--output",type=Path,required=True)
    check=bundle_sub.add_parser("validate");check.add_argument("input",type=Path)
    svg_import=sub.add_parser("import-svg");svg_import.add_argument("input",type=Path)
    svg_import.add_argument("--output",type=Path,required=True);svg_import.add_argument("--scale",type=float,default=3)
    svg_import.add_argument("--scene-version",choices=["0.2","0.3"],default="0.2")
    compose=sub.add_parser("compose");compose.add_argument("input",type=Path);compose.add_argument("--bundle",type=Path,required=True)
    compose.add_argument("--output",type=Path,required=True);compose.add_argument("--scale",type=float,default=3)
    fit=sub.add_parser("fit");fit.add_argument("input",type=Path);fit.add_argument("--output",type=Path,required=True)
    inspect=sub.add_parser("inspect");inspect.add_argument("input",type=Path)
    inspect.add_argument("--output",type=Path)
    inspect.add_argument("--analyze",action="store_true")
    inspect.add_argument("--colors",type=int,default=6)
    inspect.add_argument("--background")
    inspect.add_argument("--evidence",choices=["legacy","contrast"],default="legacy",
                         help="With --analyze: contrast skips palette, ridges and skeleton work")
    for name in ["draw","reconstruct","validate","review","edit","export","render","repair"]:
        cmd=sub.add_parser(name);cmd.add_argument("input",type=Path)
        cmd.add_argument("--device",choices=["auto","cpu","gpu"],default="auto")
        if name in {"validate","review"}:cmd.add_argument("--json",action="store_true")
        elif name in {"edit","repair"}:
            if name=="edit":cmd.add_argument("--patch",type=Path,required=True)
            else:
                cmd.add_argument("--source",type=Path,required=True)
                cmd.add_argument("--object",required=True)
                cmd.add_argument("--request",type=Path,required=True)
            cmd.add_argument("--output",type=Path)
            cmd.add_argument("--scale",type=float,default=3)
        else:
            cmd.add_argument("--output",type=Path,required=True)
            cmd.add_argument("--scale",type=float,default=3)
        if name in {"draw","reconstruct"}:cmd.add_argument("--context",type=Path)
        if name=="review":cmd.add_argument("--record",type=Path,help="Record a host assessment bound to the current project preview")
        if name=="reconstruct":cmd.add_argument("--plan",type=Path,required=True)
        if name=="export":
            cmd.add_argument("--objects",required=True)
            cmd.add_argument("--format",choices=["svg","png"],default="svg")
            cmd.add_argument("--padding",type=float,default=4)
    return p


def write_new(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("xb") as stream:stream.write(data)


def execute(args):
    if getattr(args,"device","auto")=="gpu":
        raise ValueError("unsupported: this prototype has CPU geometry/contour/render backends only; use --device cpu or auto")
    command=args.command
    if command=="consume":
        from .prepared import consume
        result=consume(args.input,input_ids=args.ids.split(',') if args.ids else None,views=args.views.split(','))
        if args.output:write_new(args.output,json_text(result).encode('utf-8'))
        return result
    if command=="candidates":
        from .prepared import validate_candidates
        return validate_candidates(load_json(args.input),args.source,roi=[int(v) for v in args.roi.split(',')] if args.roi else None,background=args.background)
    if command=="text":
        from .text_tools import measure_text, wrap_text, validate_inventory
        data=load_json(args.input)
        if args.action=='validate-inventory':return validate_inventory(data)
        if args.action=='measure':return measure_text(**data)
        return wrap_text(**data)
    if command=="recipe":
        from .recipes import list_recipes, describe, build
        if args.action=='list':return {'status':'success','recipes':list_recipes()}
        if args.id is None:raise ValueError('recipe ID required')
        if args.action=='describe':return describe(args.id,version=args.version)
        if args.output is None:raise ValueError('recipe build requires --output')
        from .builder import SceneBuilder
        builder=SceneBuilder(args.width,args.height,schema_version='0.3')
        result=build(builder,recipe_id=args.id,version=args.version,instance_id=args.instance,params=load_json(args.params) if args.params else {})
        scene=builder.build()
        from .geometry import Geometry
        box=Geometry(scene).bounds(result['root_id'])
        if box is not None:
            x0,y0,x1,y1=box
            scene['canvas']['viewBox']=[x0-8,y0-8,x1-x0+16,y1-y0+16]
        receipt=write_project(scene,args.output,scale=args.scale)
        receipt['recipe']=result
        return receipt
    if command=="registry":
        from .registry import list_entries, resolve, search
        if args.action=='search':
            if args.id is None:raise ValueError('registry search requires request JSON file')
            return search(load_json(Path(args.id)),registry=args.registry)
        if args.action=='list':return {'status':'success','entries':list_entries(registry=args.registry)}
        if args.id is None:raise ValueError('registry ID required')
        options={'registry':args.registry}
        if args.version:options['version']=args.version
        return resolve(args.id,**options)
    if command=="plan":
        from .planning import compile_plan, validate_plan, validate_planning, plan_review, write_candidate
        if args.action=='review':
            from .project import project_extras
            scene=load_project(args.input);extras=project_extras(args.input);validate_planning(scene,extras)
            if 'figure-plan.json' not in extras:raise ValueError('project has no FigurePlan')
            generation=extras['generation.json'];inputs=generation['input']['inputs']
            return plan_review(scene,extras['figure-plan.json'],inputs['object_map'],planning_context=inputs.get('planning_context'),baseline=generation['baseline'])
        data=load_json(args.input)
        from .planning_tools import draft_plan, context_for, check_plan, suggest
        if args.action=='suggest':return suggest(data)
        if args.action=='draft':
            if args.output is None:raise ValueError('plan draft requires --output')
            from .evidence import _staged_directory
            draft=draft_plan(data);context=context_for(draft,data['purpose'])
            with _staged_directory(args.output) as (stage,target):
                (stage/'figure-plan.json').write_text(json_text(draft),encoding='utf-8')
                (stage/'planning-context.json').write_text(json_text(context),encoding='utf-8')
                (stage/'check.json').write_text(json_text(check_plan(draft,context=context)),encoding='utf-8')
            return {'status':'drafted','output':str(target),'semantic_understanding':'host_supplied'}
        if args.action=='validate':return validate_plan(data)
        context=load_json(args.context) if args.context else None
        fragments=load_json(args.fragments) if args.fragments else None
        if args.action=='check':return check_plan(data,context=context,phase=args.phase,fragments=fragments)
        if args.output is None:raise ValueError('plan compile requires --output')
        scene,extras,report=compile_plan(data,fragments=fragments,planning_context=context)
        reference=None
        if args.reference:
            from PIL import ImageOps
            from .evidence import _read_bounded,MAX_INPUT_BYTES,MAX_INPUT_PIXELS
            from io import BytesIO
            with Image.open(BytesIO(_read_bounded(args.reference,MAX_INPUT_BYTES))) as raw:
                if raw.width*raw.height>MAX_INPUT_PIXELS:raise ValueError('reference pixel budget exceeded')
                reference=ImageOps.exif_transpose(raw).convert('RGBA')
                reference.info.clear()
        receipt=write_candidate(scene,extras,args.output) if args.candidate else write_project(scene,args.output,scale=args.scale,extras=extras,reference=reference)
        receipt['plan_review']=report
        return receipt
    if command=="template":
        from .planning import template_candidate, write_candidate
        scene,extras,report=template_candidate(args.input,load_json(args.updates))
        receipt=write_candidate(scene,extras,args.output);receipt['plan_review']=report
        return receipt
    if command=="evidence":
        if args.input == Path('measure'):
            from .measurements import measure
            if args.request is None or args.output is None:raise ValueError('evidence measure requires request and --output')
            return measure(load_json(args.request),args.output,base_directory=args.request.resolve().parent)
        if args.request is not None:raise ValueError('unexpected evidence argument')
        from .evidence import generate_evidence, validate_evidence
        if args.validate:return validate_evidence(args.input)
        if args.output is None:raise ValueError("arguments: evidence generation requires --output")
        region=[int(v) for v in args.roi.split(",")] if args.roi else None
        return generate_evidence(args.input,args.output,channels=args.channels.split(","),region=region,background=args.background)
    if command=="prepare":
        from .preparation import prepare
        return prepare(args.input,args.output)
    if command=="generation":
        from .generation import accept_generation, diff_generation
        if args.action=="diff":return diff_generation(args.input,args.candidate,args.output)
        return accept_generation(args.input,args.candidate,base_revision=args.base_revision)
    if command=="bundle":
        from .bundles import create_bundle, validate_bundle
        return create_bundle(args.input,args.output) if args.action=="create" else validate_bundle(args.input)
    if command=="import-svg":
        from .svg_import import import_svg
        return write_project(import_svg(args.input,schema_version=args.scene_version),args.output,scale=args.scale)
    if command=="compose":
        from .composition import compose
        return compose(args.input,args.bundle,args.output,scale=args.scale)
    if command=="fit":
        from .curves import fit_open_path, smooth_path
        data=load_json(args.input)
        if set(data)-{"schema_version","kind","points","tolerance"} or data.get("schema_version")!="0.1" or data.get("kind") not in {"open","closed"}:
            raise ValueError("fit requires version 0.1, open/closed kind and points")
        import numpy as np
        points=np.asarray(data.get("points"),dtype=float)
        if points.ndim!=2 or points.shape[1]!=2 or not 2<=len(points)<=40000 or not np.isfinite(points).all():
            raise ValueError("fit requires 2..40000 finite two-dimensional points")
        tolerance=data.get("tolerance",0.35)
        if data["kind"]=="open":
            path=fit_open_path(points,tolerance=tolerance)
            details={"limitation":"Sampled fit only; intersections and scientific topology require host review"}
        else:
            if len(points)<3:raise ValueError("closed fit requires at least three points")
            polygon="M"+" L".join(f"{x},{y}" for x,y in points)+" Z"
            path,details=smooth_path(polygon,tolerance=tolerance)
        result={"status":"success","schema_version":"0.1","geometry":{"d":path},"details":details}
        write_new(args.output,json_text(result).encode("utf-8"));return result
    if command in {"context","style-check"}:
        from .context import EXTRA_NAMES, apply_context, style_check
        scene=load_project(args.input)
        if command=="style-check":
            extras={name:load_json(args.input/name) for name in PROJECT_JSON_EXTRAS if (args.input/name).is_file()}
            return style_check(scene,extras)
        if args.base_revision!=scene["revision"]:raise ValueError("revision_conflict")
        scene,updates=apply_context(scene,args.context,resolve_tokens=False)
        scene["revision"]+=1
        extras={name:load_json(args.input/name) for name in PROJECT_JSON_EXTRAS-EXTRA_NAMES if (args.input/name).is_file()}
        extras.update(updates)
        target=args.output or args.input
        reference=None
        if (args.input/"reference.png").is_file():
            with Image.open(args.input/"reference.png") as image:reference=image.copy()
        receipt=load_json(args.input/"receipt.json")
        return write_project(scene,target,scale=receipt.get("render_scale",3),reference=reference,extras=extras,
                             expected_revision=args.base_revision if target.resolve()==args.input.resolve() else None)
    if command=="doctor":
        from .pathops_backend import backend_info
        return {"status":"success","version":__version__,"runtime":runtime_info(),"optional_geometry_backend":backend_info(),
                "capabilities":{"scene_svg_png":True,"region_contours":True,"object_edit_export":True,
                                "adaptive_candidates":True,"structural_patch":True,"directed_repair":True,
                                "internal_contrast_review":True,"pixel_cell_regions":True,"curved_ridge_centerlines":True,
                                "semantic_plan":True,"host_review_record":True,"resource_patch":True,
                                "visual_evidence":True,"prepare":True,"context_style":True,"asset_bundle":True,
                                "editable_composition":True,"sampled_curve_fit":True,
                                "construction_builder":True,"safe_generation":True,"exact_patch":True,
                                "scene_0_2":True,"scene_0_3":True,"batch_builder":True,"persistent_ports":True,
                                "radial_gradient":True,"stroke_dash":True,"recipe_library":True,"figure_plan":True,
                                "selected_template_update":True,"local_registry":True,"prepared_consumption":True,
                                "plan_draft_check_suggest":True,"registry_search":True,"local_measurements":True,
                                "text_measure_wrap":True,"composition_context":True,"vector_clip_alpha_mask":True,"local_occlusions":True,"bounded_svg_import":True,
                                "runtime_preflight_integrated":False,
                                "ocr":False,"automatic_semantic_recognition":False,"gpu":False},
                "device_policy":"auto uses CPU because implemented backends are CPU-only; explicit gpu is rejected"}
    if command=="inspect":
        if args.evidence!="legacy" and not args.analyze:
            raise ValueError("--evidence requires --analyze")
        with Image.open(args.input) as image:
            result={"status":"success","sha256":digest(args.input),"size":list(image.size),
                    "mode":image.mode,"semantic_analysis":"host_required","ocr":"not_implemented"}
        if args.analyze:
            from .analysis import analyze
            result["analysis"]=analyze(args.input,colors=args.colors,background=args.background,
                                       evidence=["contrast"] if args.evidence=="contrast" else None)
        if args.output:write_new(args.output,json_text(result).encode("utf-8"))
        return result
    if command=="draw":
        data=load_json(args.input)
        if data.get("schema_version")=="0.3" and "steps" in data:
            if "source" in data:
                raise ValueError("source-bound plans require reconstruct with the reference image")
            scene=reconstruct(None,data)
        else:scene=validate(data)
        extras={}
        if args.context:
            from .context import apply_context
            scene,extras=apply_context(scene,args.context,resolve_tokens=True)
        return write_project(scene,args.output,scale=args.scale,extras=extras)
    if command=="reconstruct":
        plan=load_json(args.plan);scene=reconstruct(args.input,plan)
        extras={"reconstruction-plan.json":plan}
        if args.context:
            from .context import apply_context
            scene,context_extras=apply_context(scene,args.context,resolve_tokens=True);extras.update(context_extras)
        x,y,w,h=plan["source"]["region"]
        with Image.open(args.input) as image:reference=image.convert("RGBA").crop((x,y,x+w,y+h))
        return write_project(scene,args.output,scale=args.scale,reference=reference,extras=extras)
    if command=="render":
        svg=args.input.read_text(encoding="utf-8")
        write_new(args.output,render_svg(svg,args.scale))
        return {"status":"success","output":str(args.output.resolve()),"runtime":runtime_info()}
    scene=load_project(args.input) if args.input.is_dir() else validate(load_json(args.input))
    if command=="validate":
        return {"status":"success","revision":scene["revision"],"object_count":len(scene["objects"]),
                "quality_status":"needs_review","object_ids":[o["id"] for o in scene["objects"]]}
    if command=="review":
        from .quality import scene_review
        from .evaluation import current_audit
        from .reviews import current_project_review, validate_record
        host_review=None
        if args.input.is_dir():
            host_review=current_project_review(scene,args.input)
            if args.record:
                record=validate_record(load_json(args.record),scene,host_review["binding"])
                receipt=load_json(args.input/"receipt.json")
                write_project(scene,args.input,scale=receipt.get("render_scale",3),
                              extras={"review.json":{"host_record":record}},expected_revision=scene["revision"])
                host_review=current_project_review(scene,args.input)
        elif args.record:
            raise ValueError("host review recording requires an engine project directory")
        return {"status":"success","drawing_review":scene_review(scene),
                "adaptive_review":current_audit(scene) if "adaptive_context" in scene.get("provenance",{}) else None,
                "host_review":host_review,"scientific_review":"host_required"}
    if command in {"edit","repair"}:
        if not args.input.is_dir():raise ValueError("edit/repair expects an engine project directory")
        repair_report=None
        if command=="repair":
            from .repair import repair_scene
            edited,repair_report=repair_scene(scene,args.source,args.object,load_json(args.request))
        else:edited=apply_patch(scene,load_json(args.patch))
        target=args.output or args.input
        in_place=target.resolve()==args.input.resolve()
        reference=None
        if (args.input/"reference.png").is_file():
            with Image.open(args.input/"reference.png") as image:reference=image.copy()
        extras={}
        for name in PROJECT_JSON_EXTRAS-{"analysis.json","candidates.json"}:
            if (args.input/name).is_file():extras[name]=load_json(args.input/name)
        if repair_report is not None:extras["repair.json"]=repair_report
        result=write_project(edited,target,scale=args.scale,reference=reference,extras=extras,
                             expected_revision=scene["revision"] if in_place else None)
        if repair_report is not None:result["repair_status"]=repair_report["status"]
        return result
    if command=="export":
        selected=export_scene(scene,args.objects.split(","),args.padding)
        svg=to_svg(selected)
        data=svg.encode("utf-8") if args.format=="svg" else render_svg(svg,args.scale)
        write_new(args.output,data)
        return {"status":"success","output":str(args.output.resolve()),"selected":args.objects.split(","),
                "object_count":len(selected["objects"]),"canvas":selected["canvas"]}
    raise ValueError("unknown command")


def main(argv=None):
    try:
        result=execute(parser().parse_args(argv))
        print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        return 0
    except (ValueError,TypeError,KeyError,OSError,ImportError) as error:
        text=str(error)
        status="needs_input" if text.startswith("needs_input:") else "unsupported" if text.startswith("unsupported:") else "failed"
        print(json.dumps({"status":status,"error":{"type":type(error).__name__,"message":text}},ensure_ascii=False))
        return {"needs_input":3,"unsupported":4,"failed":2}[status]


if __name__=="__main__":sys.exit(main())

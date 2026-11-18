"""Staged project writes, revision checking, byte-bound receipts and visual QA."""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont
from .fonts import font_info
from .render import render_svg, runtime_info
from .scene import load_json, manifest, validate
from .svg import to_svg


def json_text(data):
    return json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+"\n"


PROJECT_JSON_EXTRAS = frozenset({"reconstruction-plan.json", "analysis.json", "candidates.json", "review.json", "repair.json",
                                "context.json", "style-profile.json", "text-manifest.json", "layout-manifest.json", "generation.json", "figure-plan.json", "composition-context.json"})


def adaptive_extras(scene):
    context=scene.get("provenance",{}).get("adaptive_context")
    if not context:
        return {}
    from .evaluation import current_audit
    return {"analysis.json":context["analysis"],
            "candidates.json":{"schema_version":"0.2","steps":[
                {"step_id":step["step_id"],"analysis":step["analysis"],"search":step["candidates"]}
                for step in scene["provenance"].get("steps",[]) if "candidates" in step]},
            "review.json":{"schema_version":"0.2","revision":scene["revision"],
                           "mechanical":current_audit(scene),"visual":"host_required","scientific":"host_required"}}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextmanager
def project_lock(output):
    lock=output.parent/("."+output.name+".sf-lock")
    descriptor=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    try:
        os.write(descriptor,str(os.getpid()).encode());os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)


def comparison(reference, preview, output):
    reference=reference.convert("RGB")
    actual=preview.convert("RGBA")
    white=Image.new("RGBA",actual.size,"white");white.alpha_composite(actual)
    actual=white.convert("RGB").resize(reference.size,Image.Resampling.LANCZOS)
    difference=ImageChops.difference(reference,actual)
    mae=float(np.abs(np.asarray(reference,dtype=float)-np.asarray(actual,dtype=float)).mean()/255)
    scale=max(1,min(4,480//max(reference.size)))
    w,h=reference.width*scale,reference.height*scale
    panel=Image.new("RGB",(w*3+32,h+44),"#f4f5f7")
    draw=ImageDraw.Draw(panel)
    font=ImageFont.truetype(font_info()["path"],14)
    for i,(image,label) in enumerate([(reference,"Reference crop"),(actual,"Reconstructed SVG"),(difference,"Absolute pixel difference")]):
        x=8+i*(w+8)
        panel.paste(image.resize((w,h)),(x,34));draw.text((x,9),label,font=font,fill="#222222")
    panel.save(output)
    return {"normalized_rgb_mae":round(mae,6),"meaning":"pixel difference only; not a scientific correctness score"}


def project_extras(path):
    """Read the portable JSON closure; validation belongs to load/write_project."""
    root = Path(path)
    return {name: load_json(root/name) for name in PROJECT_JSON_EXTRAS if (root/name).is_file()}


def project_state_sha256(scene, extras):
    """Canonical state binding, including inert generation and context snapshots."""
    from .reviews import content_digest
    return content_digest({"scene": scene, "extras": extras})


def write_project(scene, output, *, scale=3, reference=None, extras=None, expected_revision=None,
                  expected_state_sha256=None, expected_receipt_sha256=None):
    validate(scene)
    audit = None
    if scene.get("provenance", {}).get("mode") == "semantic":
        from .reconstruct import semantic_audit
        audit = semantic_audit(scene)
        if audit["status"] not in {"passed", "needs_review"}:
            raise ValueError("semantic mechanical audit rejected: " + ", ".join(audit["reasons"]))
    output=Path(output).resolve()
    output.parent.mkdir(parents=True,exist_ok=True)
    with project_lock(output):
        if output.exists():
            if expected_revision is None:raise ValueError("output already exists; use edit for revision-checked updates")
            current=load_project(output)
            if current["revision"]!=expected_revision:raise ValueError("revision_conflict")
            if (expected_state_sha256 is not None
                    and project_state_sha256(current, project_extras(output)) != expected_state_sha256):
                raise ValueError("generation stale current project content")
            if expected_receipt_sha256 is not None and digest(output/"receipt.json") != expected_receipt_sha256:
                raise ValueError("generation stale current project receipt")
        elif expected_revision is not None or expected_state_sha256 is not None or expected_receipt_sha256 is not None:
            raise ValueError("project disappeared before edit commit")
        stage=Path(tempfile.mkdtemp(prefix="."+output.name+".stage-",dir=output.parent))
        backup=None
        try:
            svg=to_svg(scene)
            (stage/"scene.json").write_text(json_text(scene),encoding="utf-8")
            (stage/"figure.svg").write_text(svg,encoding="utf-8")
            (stage/"preview.png").write_bytes(render_svg(svg,scale))
            inventory=manifest(scene)
            (stage/"objects.json").write_text(json_text(inventory),encoding="utf-8")
            qa={"execution":"completed","geometry_validation":"passed","vector_only":True,
                "object_count":len(scene["objects"]),
                "path_count":sum(o["kind"]=="path" for o in scene["objects"]),
                "text_count":sum(o["kind"]=="text" for o in scene["objects"]),
                "scientific_review":"needs_review",
                "limitations":["No automatic scientific correctness judgement.",
                               "OCR is not implemented; text was supplied by the host.",
                               "Only the declared SVG subset is supported."]}
            from .quality import scene_review
            qa["drawing_review"]=scene_review(scene)
            if audit is not None:
                qa["semantic_audit"] = audit
            if scene.get("schema_version") in {"0.2", "0.3"}:
                from .scene_effects import evaluate
                qa["effect_evaluation"] = evaluate(scene)
            if reference is None and output.exists() and (output/"reference.png").exists():
                with Image.open(output/"reference.png") as image:reference=image.copy()
            if reference is not None:
                reference.save(stage/"reference.png")
                with Image.open(stage/"preview.png") as image:
                    qa["comparison"]=comparison(reference,image,stage/"comparison.png")
            if output.exists():
                for name in PROJECT_JSON_EXTRAS:
                    if (output/name).is_file():shutil.copyfile(output/name,stage/name)
            # Preserve the host's declaration when rebuilding/cloning a project;
            # a changed scene or preview makes it stale, never silently approved.
            previous_review = (extras or {}).get("review.json")
            if previous_review is None and (stage/"review.json").is_file():
                previous_review = load_json(stage/"review.json")
            additions={**adaptive_extras(scene),**(extras or {})}
            from .context import EXTRA_NAMES, validate_context
            if "context.json" in (extras or {}):
                # An explicit context snapshot replaces its whole document closure.
                for name in EXTRA_NAMES:
                    (stage/name).unlink(missing_ok=True)
            if "context" in scene.get("provenance",{}) or EXTRA_NAMES.intersection(additions) or any((stage/name).is_file() for name in EXTRA_NAMES):
                documents={name:load_json(stage/name) for name in PROJECT_JSON_EXTRAS if (stage/name).is_file()}
                documents.update(additions)
                validate_context(scene,documents)
            from .generation import validate_generation
            documents = {name:load_json(stage/name) for name in PROJECT_JSON_EXTRAS if (stage/name).is_file()}
            documents.update(additions)
            validate_generation(scene, documents)
            from .composition_context import validate_composition_context
            validate_composition_context(scene, documents)
            if "figure-plan.json" in documents or "figure_plan_sha256" in documents.get("generation.json", {}).get("input", {}).get("inputs", {}):
                from .planning import validate_planning
                validate_planning(scene, documents)
                from .planning_tools import validate_reference_view
                validate_reference_view(documents, stage)
                from .planning import plan_review
                generation = documents['generation.json']; inputs = generation['input']['inputs']
                qa['plan_review'] = plan_review(scene, documents['figure-plan.json'], inputs['object_map'], planning_context=inputs.get('planning_context'), baseline=generation['baseline'])
            from .reviews import artifact_binding, review_document
            render_runtime = runtime_info()
            binding = artifact_binding(scene, stage/"preview.png", stage/"reference.png", render_runtime, scale)
            additions["review.json"] = review_document(scene, binding, previous_review)
            for name,content in additions.items():
                if name not in PROJECT_JSON_EXTRAS:raise ValueError("unsupported project extra")
                (stage/name).write_text(json_text(content),encoding="utf-8")
            if "adaptive_context" in scene.get("provenance",{}) and reference is not None:
                actual=Image.open(stage/"preview.png").convert("RGBA").resize(reference.size,Image.Resampling.LANCZOS)
                source=reference.convert("RGBA")
                Image.blend(source,actual,0.5).save(stage/"overlay.png")
                from .evaluation import current_audit
                qa["adaptive_review"]=current_audit(scene)
            (stage/"qa.json").write_text(json_text(qa),encoding="utf-8")
            receipt={"schema_version":"0.1","status":"success","quality_status":"needs_review",
                     "revision":scene["revision"],"runtime":render_runtime,"render_scale":scale,
                     "artifacts":{p.name:{"sha256":digest(p),"bytes":p.stat().st_size} for p in sorted(stage.iterdir())}}
            (stage/"receipt.json").write_text(json_text(receipt),encoding="utf-8")
            if output.exists():
                backup=Path(tempfile.mkdtemp(prefix="."+output.name+".backup-",dir=output.parent))
                backup.rmdir();output.rename(backup)
            try:
                stage.rename(output)
            except BaseException:
                if backup is not None:backup.rename(output);backup=None
                raise
            if backup is not None:shutil.rmtree(backup)
            return {"status":"success","quality_status":"needs_review","project":str(output),
                    "revision":scene["revision"],"object_count":len(scene["objects"])}
        finally:
            if stage.exists():shutil.rmtree(stage)


def load_project(path):
    path=Path(path)
    receipt=load_json(path/"receipt.json")
    for name,info in receipt["artifacts"].items():
        if Path(name).name!=name or digest(path/name)!=info["sha256"]:
            raise ValueError(f"project artifact changed outside engine: {name}")
    scene=load_json(path/"scene.json")
    if scene["revision"]!=receipt["revision"]:raise ValueError("receipt revision mismatch")
    from .context import EXTRA_NAMES, validate_context
    if "context" in scene.get("provenance",{}) or any((path/name).is_file() for name in EXTRA_NAMES):
        validate_context(scene,{name:load_json(path/name) for name in PROJECT_JSON_EXTRAS if (path/name).is_file()})
    from .generation import validate_generation
    if (path/"generation.json").is_file() and "generation.json" not in receipt["artifacts"]:
        raise ValueError("unreceipted generation.json")
    documents = project_extras(path)
    for name in ("figure-plan.json", "composition-context.json"):
        if name in documents and name not in receipt["artifacts"]:
            raise ValueError("unreceipted " + name)
    validate_generation(scene, documents)
    from .composition_context import validate_composition_context
    validate_composition_context(scene, documents)
    if "figure-plan.json" in documents or "figure_plan_sha256" in documents.get("generation.json", {}).get("input", {}).get("inputs", {}):
        from .planning import validate_planning
        validate_planning(scene, documents)
        from .planning_tools import validate_reference_view
        validate_reference_view(documents, path)
    return validate(scene)

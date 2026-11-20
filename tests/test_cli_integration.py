"""Exercise the installed CLI from a different cwd, with no display or host model."""
import hashlib
import json
import os
import subprocess
import sys
from PIL import Image,ImageDraw


def test_installed_cli_end_to_end(tmp_path):
    source=tmp_path/"source.png"
    image=Image.new("RGB",(100,90),"white");ImageDraw.Draw(image).ellipse((10,10,90,65),fill="#268b66");image.save(source)
    plan={"schema_version":"0.1","source":{"sha256":hashlib.sha256(source.read_bytes()).hexdigest(),"size":[100,90],"region":[0,0,100,90]},
          "canvas":{"width":100,"height":90,"background":"#ffffff"},"steps":[
              {"step_id":"shape","operation":"trace_region","object":{"id":"shape","kind":"group"},"region":[0,0,100,90],"parameters":{"palette":["#ffffff","#268b66"]}},
              {"step_id":"label","operation":"construct","object":{"id":"label","kind":"text","geometry":{"x":10,"y":83,"text":"Cell"},"style":{"font_size":12}}}]}
    plan_file=tmp_path/"plan.json";plan_file.write_text(json.dumps(plan))
    env={**os.environ,"DISPLAY":""}
    def run(*args,expected=0):
        result=subprocess.run([sys.executable,"-I","-m","scientific_figure.cli",*map(str,args)],cwd=tmp_path,env=env,capture_output=True,text=True)
        assert result.returncode==expected,(result.stdout,result.stderr)
        return json.loads(result.stdout)
    assert run("doctor","--json")["runtime"]["device"]=="cpu"
    project=tmp_path/"project"
    run("reconstruct",source,"--plan",plan_file,"--output",project)
    assert run("validate",project)["object_count"]==3
    assert run("review",project)["drawing_review"]["status"]=="passed"
    assert (project/"preview.png").read_bytes().startswith(b"\x89PNG")
    run("export",project,"--objects","shape","--format","svg","--output",tmp_path/"shape.svg")
    assert "<text" not in (tmp_path/"shape.svg").read_text()
    run("render",tmp_path/"shape.svg","--output",tmp_path/"shape.png")
    patch=tmp_path/"patch.json";patch.write_text(json.dumps({"base_revision":0,"operations":[{"id":"label","set":{"geometry":{"text":"Nucleus"}}}]}))
    run("edit",project,"--patch",patch)
    assert run("validate",project)["revision"]==1
    assert run("review",project)["drawing_review"]["status"]=="stale_after_edit"
    run("edit",project,"--patch",patch,expected=2)
    assert run("validate",project)["revision"]==1


def test_layout_audit_cli_reports_and_fixes_a_copy(tmp_path):
    from scientific_figure.builder import SceneBuilder
    from scientific_figure.project import write_project

    builder=SceneBuilder(600,300,schema_version="0.3")
    builder.rect("card",40,100,140,90,style={"fill":"#ffffff","stroke":"#88aacc","stroke_width":2},semantic={"role":"panel"})
    builder.text("label",110,145,"A very long label that will not fit inside this card",style={"font_size":14,"anchor":"middle"})
    project=tmp_path/"project"
    write_project(builder.build(),project)
    original=(project/"scene.json").read_bytes()

    env={**os.environ,"DISPLAY":""}
    def audit(*args,expected=0):
        result=subprocess.run([sys.executable,"-I","-m","scientific_figure.cli","layout-audit",
                               *map(str,args)],cwd=tmp_path,env=env,capture_output=True,text=True)
        assert result.returncode==expected,(result.stdout,result.stderr)
        return json.loads(result.stdout)

    failing=audit(project,"--target-width-mm",183,"--output",tmp_path/"qa.json",
                  "--overlay",tmp_path/"qa.overlay.svg",expected=1)
    assert failing["verdict"]=="failed"
    assert failing["summary"]["fail"]>=1
    assert json.loads((tmp_path/"qa.json").read_text(encoding="utf-8"))["kind"]=="scene_qa"
    assert 'id="qa-overlay"' in (tmp_path/"qa.overlay.svg").read_text(encoding="utf-8")

    fixed=audit(project,"--target-width-mm",183,"--fix","--fix-output",tmp_path/"fixed")
    assert fixed["fix_status"] in {"fixed","partially_fixed"}
    assert fixed["verdict"] in {"clear","needs_review"}
    assert fixed["fix_output"]["revision"]==1
    assert (project/"scene.json").read_bytes()==original

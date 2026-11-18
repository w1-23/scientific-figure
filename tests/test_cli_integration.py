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

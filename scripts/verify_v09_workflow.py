"""Public, source-free CLI integration example, also runnable from installed wheel.

Uses original synthetic pixels, no private artwork. Automated assertions do not
constitute a host visual review. --require-wheel verifies every application file.
"""
from __future__ import annotations
import argparse
import copy
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
import zipfile
from PIL import Image, ImageDraw
import scientific_figure
from scientific_figure.cli import execute, parser
from scientific_figure.context import canonical_hash
from scientific_figure.planning_tools import context_for
from scientific_figure.project import digest, json_text, load_project, project_extras, write_project
from scientific_figure.reviews import current_project_review


def run(output, *, require_wheel=False):
    out=Path(output).resolve();out.mkdir(parents=True,exist_ok=False)
    module=Path(scientific_figure.__file__).resolve()
    installation={'module':str(module),'prefix':sys.prefix,'version':scientific_figure.__version__}
    if require_wheel:
        assert module.is_relative_to(Path(sys.prefix).resolve()),module
        wheel=Path(__file__).resolve().parents[1]/f'deliverables/scientific_figure_skill-{scientific_figure.__version__}-py3-none-any.whl'
        with zipfile.ZipFile(wheel) as z:
            count=0
            for name in z.namelist():
                if name.startswith('scientific_figure/') and not name.endswith('/'):
                    assert (module.parent/name.removeprefix('scientific_figure/')).read_bytes()==z.read(name),name
                    count+=1
        installation.update(wheel_sha256=digest(wheel),application_files_matched=count)
    events=[]
    def save(name,value):
        path=out/name;path.write_text(json_text(value),encoding='utf-8');return path
    def cli(*args):
        result=execute(parser().parse_args([str(a) for a in args]));events.append({'command':str(args[0]),'status':result.get('status','completed')});return result
    assert cli('doctor')['capabilities']['local_measurements']
    catalog=cli('registry','search',save('search.json',{'query':'细胞','required_parts':['membrane','mitochondrion'],'limit':3}))
    cell=next(c for c in catalog['candidates'] if c['id']=='cell_symbol')
    assert cell['fit']=='partial' and 'mitochondrion' in cell['missing_parts']
    assert cli('plan','suggest',save('absent.json',{'query':'mouse'}))['candidates']==[]
    inputs=out/'original-source';inputs.mkdir();source=inputs/'synthetic.png'
    image=Image.new('RGB',(120,80),'white');draw=ImageDraw.Draw(image)
    draw.ellipse((10,10,40,40),fill='#223344');draw.ellipse((17,17,33,33),fill='white')
    draw.ellipse((65,10,95,40),fill='#223344');draw.ellipse((72,17,88,33),fill='white')
    draw.rectangle((10,55,100,66),fill='#bb3355');image.save(source)
    measurements=[]
    for kind,roi,params in [
        ('view',[5,5,45,40],{'context_margin':4}),
        ('profile',[5,5,100,65],{'points':[[25,25],[20,12]],'lines':[{'start':[10,60],'end':[100,60],'samples':20}]}),
        ('topology',[5,5,45,40],{'foreground':'dark','thresholds':[90,110]}),
        ('repeat',[5,5,100,42],{'template_roi':[8,8,35,35],'threshold':0.99,'scale':1})]:
        req=save(f'{kind}-request.json',{'schema_version':'0.1','source':{'path':str(source),'sha256':digest(source)},'roi':roi,'question':f'Host-selected {kind} observation, no semantics.','kind':kind,'background':'#ffffff','params':params})
        measurements.append(cli('evidence','measure',req,'--output',out/f'measure-{kind}'))
    request={'id':'public_workflow','task_type':'text_design','purpose':'Two declared process nodes; geometry is host authored.',
        'canvas':{'width':500,'height':200,'background':'#ffffff'},'sources':[{'id':'synthetic','sha256':digest(source),'size':[120,80]}],
        'modules':[{'id':'main','objects':[{'id':'input','role':'Input','editable_parts':['body','label']},{'id':'output','role':'Output','editable_parts':['body','label']}]}],
        'relations':[{'id':'transfer','type':'points_to','mode':'execute','source':{'object_id':'input'},'target':{'object_id':'output'}}],
        'review_spec':{'required_objects':['input','output'],'scientific_review':'not_executed','text_policy':'host_authored'}}
    cli('plan','draft',save('draft-request.json',request),'--output',out/'draft')
    plan=json.loads((out/'draft/figure-plan.json').read_text(encoding='utf-8'))
    assert cli('plan','check',out/'draft/figure-plan.json','--phase','construction')['status']=='needs_input'
    for i,obj in enumerate(plan['modules'][0]['objects']):
        obj['construction']={'kind':'recipe','id':'flow_node','version':'1.0.0','params':{'label':['Input','Output'][i]}}
        obj['transform']=[1,0,0,1,30+i*280,60]
    context=context_for(plan,request['purpose']);context['evidence']=measurements
    for obj in plan['modules'][0]['objects']:
        context['objects'][obj['id']]={'construction_sha256':canonical_hash(obj['construction']),'reason':'Flat measured process card preserves the requested label; no scientific content inferred.','strategies':['filled_shape','text'],'confirmed':True,'evidence_keys':[measurements[0]['result_sha256']]}
    planfile=save('figure-plan.json',plan);contextfile=save('planning-context.json',context)
    assert cli('plan','check',planfile,'--context',contextfile,'--phase','construction')['status']=='compilable'
    cli('plan','compile',planfile,'--context',contextfile,'--output',out/'project','--scale',1)
    scene=load_project(out/'project');extras=project_extras(out/'project');mapping=extras['generation.json']['input']['inputs']['object_map']
    patch={'base_revision':0,'operations':[
        {'id':mapping['input']['part_ids']['body'],'set':{'style':{'fill':'#e5b968'}}},
        {'id':'input','set':{'transform':[1,0,0,1,40,75]}},
        {'id':mapping['output']['part_ids']['label'],'set':{'geometry':{'text':'User output'}}}]}
    cli('edit',out/'project','--patch',save('user-edit.json',patch),'--scale',1)
    cli('template','candidate',out/'project','--updates',save('update.json',{'input':{'params':{'width':160}}}),'--output',out/'candidate')
    assert cli('generation','diff',out/'project','--candidate',out/'candidate','--output',out/'diff')['status']=='ready'
    cli('generation','accept',out/'project','--candidate',out/'diff','--base-revision',1)
    current=load_project(out/'project');nodes={o['id']:o for o in current['objects']}
    assert nodes['input']['transform']==[1,0,0,1,40,75]
    assert nodes[mapping['input']['part_ids']['body']]['style']['fill']=='#e5b968'
    assert nodes[mapping['output']['part_ids']['label']]['geometry']['text']=='User output'
    report=cli('plan','review',out/'project');assert report['trace'][1]['text_inventory'][0]['differs_from_input']
    save('plan-review.json',report)
    for fmt in ['svg','png']:cli('export',out/'project','--objects','input','--format',fmt,'--output',out/f'input.{fmt}','--scale',1)
    cli('bundle','create',save('bundle-plan.json',{'schema_version':'0.1','assets':[{'id':'workflow','project':'project'}]}),'--output',out/'bundle')
    cli('bundle','validate',out/'bundle')
    compose={'schema_version':'0.2','canvas':{'width':1000,'height':450,'background':'#ffffff'},'placements':[
        {'instance_id':'a','asset_id':'workflow','transform':[1,0,0,1,0,0],'asset_background':'omit'},
        {'instance_id':'b','asset_id':'workflow','transform':[1,0,0,1,450,200],'asset_background':'omit'}]}
    cli('compose',save('composition.json',compose),'--bundle',out/'bundle','--output',out/'composed','--scale',1)
    composition=project_extras(out/'composed')['composition-context.json']
    raw=json.dumps(composition,ensure_ascii=False)
    assert 'planning_context' in raw and measurements[0]['result_sha256'] in raw
    inputs.rename(out/'source-moved')
    assert load_project(out/'project')==current
    load_project(out/'composed');cli('bundle','validate',out/'bundle')
    proof={'status':'passed','installation':installation,'events':events,'measurements':[{'kind':m['kind'],'status':m['status'],'result_sha256':m['result_sha256']} for m in measurements],
        'edit_merge_export_reopen':True,'planning_context_survives_source_move_and_composition':True,
        'visual_review':'not_executed_by_automated_script','scientific_review':'not_executed'}
    save('verification.json',proof);return proof


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--require-wheel',action='store_true');a=p.parse_args()
    print(json.dumps(run(a.output,require_wheel=a.require_wheel),ensure_ascii=False),flush=True)

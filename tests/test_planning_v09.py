"""Host intent checks and portable inert decisions, using real project transactions."""
import copy
from io import BytesIO
import pytest
from PIL import Image
from scientific_figure.context import canonical_hash
from scientific_figure.planning_tools import draft_plan, context_for, validate_context, check_plan
from scientific_figure.planning import compile_plan, plan_review, template_candidate, write_candidate
from scientific_figure.project import write_project, load_project, project_extras
from scientific_figure.generation import diff_generation, accept_generation
from scientific_figure.editing import apply_patch
from scientific_figure.cli import execute, parser


def request():
    return {'id':'diagram','task_type':'text_design','purpose':'Host supplied input to output',
        'canvas':{'width':440,'height':180,'background':'#ffffff'},
        'modules':[{'id':'main','objects':[{'id':'input','role':'input','editable_parts':['body','label']}]}]}


def resolved():
    plan=draft_plan(request())
    obj=plan['modules'][0]['objects'][0]
    obj['construction']={'kind':'recipe','id':'flow_node','version':'1.0.0','params':{'label':'Input'}}
    plan['review_spec']['required_objects']=['input']
    context=context_for(plan,'Draw only supplied objects; layout is a host design choice.')
    context['objects']['input']={'construction_sha256':canonical_hash(obj['construction']),
        'reason':'Editable rectangle and label fit this flat process node.', 'strategies':['filled_shape','text'], 'confirmed':True}
    return plan,context


def test_draft_does_not_infer_steps_or_execute_unresolved():
    original=request();plan=draft_plan(original)
    assert 'construction' not in original['modules'][0]['objects'][0]
    assert len(plan['modules'][0]['objects'])==1
    assert check_plan(plan)['status']=='ready_for_host_review'
    assert check_plan(plan,phase='construction')['status']=='needs_input'
    with pytest.raises(ValueError,match='needs_input'):compile_plan(plan)
    empty=request();del empty['modules']
    assert check_plan(draft_plan(empty))['status']=='needs_input'


def test_context_rejects_stale_geometry_foreign_ids_and_sources():
    plan,context=resolved();validate_context(plan,context)
    for mutate in [lambda c:c.update(plan_sha256='a'*64),
                   lambda c:c['objects'].update(unknown=c['objects']['input']),
                   lambda c:c['objects']['input'].update(path='M0 0'),
                   lambda c:c.update(sources={'private':'b'*64}),
                   lambda c:c['objects']['input'].update(construction_sha256='c'*64)]:
        bad=copy.deepcopy(context);mutate(bad)
        with pytest.raises(ValueError):validate_context(plan,bad)
    assert check_plan(plan,context=context,phase='construction')['status']=='compilable'


def test_program_check_never_executes_source(tmp_path):
    plan,context=resolved();obj=plan['modules'][0]['objects'][0]
    obj['construction']={'kind':'program','candidate':'not_a_script','sha256':'a'*64}
    assert check_plan(plan,phase='construction')['status']=='needs_input'
    assert list(tmp_path.iterdir())==[]


def test_user_changes_remain_authoritative_across_context_candidate(tmp_path):
    plan,context=resolved();scene,extras,_=compile_plan(plan,planning_context=context)
    root=tmp_path/'project';write_project(scene,root,scale=1,extras=extras)
    mapping=extras['generation.json']['input']['inputs']['object_map']['input']
    user=apply_patch(scene,{'base_revision':0,'operations':[
        {'id':mapping['part_ids']['body'],'set':{'style':{'fill':'#cc2255'}}},
        {'id':mapping['part_ids']['label'],'set':{'geometry':{'text':'User text'}}},
        {'id':'input','set':{'transform':[1,0,0,1,25,35]}}]})
    write_project(user,root,scale=1,expected_revision=0)
    report=execute(parser().parse_args(['plan','review',str(root)]))
    trace=report['trace'][0]
    assert len(trace['changed_from_baseline'])==3
    assert trace['text_inventory'][0]['text']=='User text'
    candidate,new_extras,_=template_candidate(root,{'input':{'params':{'width':180}}})
    new_context=new_extras['generation.json']['input']['inputs']['planning_context']
    assert new_context['objects']['input']['confirmed'] is False
    source=tmp_path/'candidate';write_candidate(candidate,new_extras,source)
    # Label-to-layout dependencies require a fresh host decision rather than erasing edits.
    diff=diff_generation(root,source,tmp_path/'diff')
    assert diff['status']=='conflicts'
    assert load_project(root)==user
    assert project_extras(root)['generation.json']['input']['inputs']['planning_context']==context


def test_saved_reference_digest_is_distinct_and_required(tmp_path):
    plan,context=resolved();plan['task_type']='reference_reconstruction'
    plan['sources']=[{'id':'original','sha256':'a'*64,'size':[10,10]}]
    context=context_for(plan,'Read supplied reference, preserve text.')
    im=Image.new('RGBA',(10,10),'white');buf=BytesIO();im.save(buf,format='PNG')
    import hashlib
    context['reference_view']={'source_id':'original','sha256':hashlib.sha256(buf.getvalue()).hexdigest(),'source_to_view':[1,0,0,1,0,0]}
    scene,extras,_=compile_plan(plan,planning_context=context)
    with pytest.raises(ValueError,match='saved reference'):write_project(scene,tmp_path/'missing',scale=1,extras=extras)
    root=tmp_path/'with-reference';write_project(scene,root,scale=1,extras=extras,reference=im)
    assert load_project(root)==scene
    assert project_extras(root)['generation.json']['input']['inputs']['planning_context']['sources']['original']=='a'*64


def test_draft_cli_and_old_validation_are_compatible(tmp_path):
    import json
    source=tmp_path/'request.json';source.write_text(json.dumps(request()),encoding='utf-8')
    out=tmp_path/'work'
    result=execute(parser().parse_args(['plan','draft',str(source),'--output',str(out)]))
    assert result['status']=='drafted'
    assert {p.name for p in out.iterdir()}=={'figure-plan.json','planning-context.json','check.json'}
    assert execute(parser().parse_args(['plan','validate',str(out/'figure-plan.json')]))['status']=='valid'
    with pytest.raises(FileExistsError):execute(parser().parse_args(['plan','draft',str(source),'--output',str(out)]))

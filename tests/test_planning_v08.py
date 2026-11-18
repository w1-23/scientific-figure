"""Intent compilation and selected-template B/U/C integration, without source execution."""
import copy
import pytest
from scientific_figure.planning import compile_plan, validate_plan, template_candidate, write_candidate, validate_planning, plan_review
from scientific_figure.project import write_project, load_project, project_extras
from scientific_figure.generation import diff_generation, accept_generation
from scientific_figure.geometry import Geometry
from scientific_figure.context import canonical_hash


def request():
    return {'schema_version':'0.1','id':'workflow','task_type':'text_design', 'purpose':'Host-declared sample processing workflow',
            'canvas':{'width':460,'height':180,'background':'none'},
            'modules':[{'id':'main','objects':[
                {'id':'sample','role':'Input sample','transform':[1,0,0,1,25,45],
                 'construction':{'kind':'recipe','id':'flow_node','version':'1.0.0','params':{'label':'Sample'}}},
                {'id':'result','role':'Measured output','transform':[1,0,0,1,275,45],
                 'construction':{'kind':'recipe','id':'flow_node','version':'1.0.0','params':{'label':'Measure'}}}]}],
            'relations':[{'id':'transfer','type':'points_to','mode':'execute',
                          'source':{'object_id':'sample','anchor':'port:right'},
                          'target':{'object_id':'result','anchor':'port:left'},'style':{'stroke':'#123456','arrow_end':True}}],
            'review_spec':{'required_objects':['sample','result'],'text_policy':'host_authored','scientific_review':'not_executed'}}


def node(scene, oid): return next(n for n in scene['objects'] if n['id']==oid)


def test_recipe_plan_persists_named_ports_and_inert_binding(tmp_path):
    plan=request(); before=copy.deepcopy(plan)
    scene, extras, report=compile_plan(plan)
    assert plan==before and scene['schema_version']=='0.3'
    assert Geometry(scene).endpoints('transfer')==((145,75),(275,75))
    assert all(c['status']=='passed' for c in report['checks'])
    root=tmp_path/'project'; write_project(scene,root,scale=1,extras=extras)
    assert load_project(root)==scene and project_extras(root)['figure-plan.json']==plan
    bad=copy.deepcopy(extras);bad['figure-plan.json']['purpose']='tamper'
    with pytest.raises(ValueError,match='binding'): validate_planning(scene,bad)
    edited=copy.deepcopy(scene);edited['objects'].remove(node(edited,'sample'))
    # Review can report current deletions, rather than executing the old plan.
    assert plan_review(edited,plan,extras['generation.json']['input']['inputs']['object_map'])['checks'][0]['status']=='failed'


def test_selected_update_keeps_other_instance_and_user_color_position_text(tmp_path):
    scene,extras,_=compile_plan(request());root=tmp_path/'project';write_project(scene,root,scale=1,extras=extras)
    mapping=extras['generation.json']['input']['inputs']['object_map']
    user=copy.deepcopy(scene)
    node(user,mapping['sample']['part_ids']['body'])['style']['fill']='#ee2200'
    node(user,'sample')['transform']=[1,0,0,1,45,55]
    node(user,mapping['result']['part_ids']['label'])['geometry']['text']='User output'
    user['revision']=1;write_project(user,root,scale=1,expected_revision=0)
    candidate,new_extras,_=template_candidate(root,{'sample':{'params':{'width':180}}})
    # Pure C does not adopt user changes as its baseline.
    assert node(candidate,mapping['sample']['part_ids']['body'])['style']['fill']!='#ee2200'
    assert new_extras['figure-plan.json']['modules'][0]['objects'][1]==request()['modules'][0]['objects'][1]
    source=tmp_path/'source';write_candidate(candidate,new_extras,source)
    assert {p.name for p in source.iterdir()}=={'scene.json','generation-input.json'}
    status=diff_generation(root,source,tmp_path/'diff')
    assert status['status']=='ready',status
    accept_generation(root,tmp_path/'diff',base_revision=1)
    result=load_project(root)
    assert node(result,'sample')['transform']==[1,0,0,1,45,55]
    assert node(result,mapping['sample']['part_ids']['body'])['style']['fill']=='#ee2200'
    assert node(result,mapping['result']['part_ids']['label'])['geometry']['text']=='User output'
    assert node(result,mapping['sample']['part_ids']['body'])['geometry']['width']==180
    assert project_extras(root)['figure-plan.json']==new_extras['figure-plan.json']
    # A second update still uses pure C, preserving the same user edits.
    c2,e2,_=template_candidate(root,{'sample':{'params':{'height':80}}})
    write_candidate(c2,e2,tmp_path/'source2')
    assert diff_generation(root,tmp_path/'source2',tmp_path/'diff2')['status']=='ready'


def test_same_field_template_conflict_preserves_current(tmp_path):
    scene,extras,_=compile_plan(request());root=tmp_path/'project';write_project(scene,root,scale=1,extras=extras)
    oid=extras['generation.json']['input']['inputs']['object_map']['sample']['part_ids']['body']
    node(scene,oid)['geometry']['width']=160;scene['revision']=1
    write_project(scene,root,scale=1,expected_revision=0)
    c,e,_=template_candidate(root,{'sample':{'params':{'width':200}}});write_candidate(c,e,tmp_path/'source')
    result=diff_generation(root,tmp_path/'source',tmp_path/'diff')
    assert result['status']=='conflicts'
    with pytest.raises(ValueError):accept_generation(root,tmp_path/'diff',base_revision=1)
    assert node(load_project(root),oid)['geometry']['width']==160


def test_program_fragment_is_hash_bound_and_never_executed():
    plan=request();plan['relations']=[];plan['modules'][0]['objects']=plan['modules'][0]['objects'][:1]
    fragment={'schema_version':'0.3','revision':0,'canvas':{'width':10,'height':10},'objects':[{'id':'p','kind':'rect','geometry':{'x':0,'y':0,'width':10,'height':10}}]}
    spec=plan['modules'][0]['objects'][0];spec['construction']={'kind':'program','candidate':'host_output','sha256':canonical_hash(fragment)}
    plan['review_spec']['required_objects']=['sample']
    with pytest.raises(ValueError,match='not supplied'): compile_plan(plan)
    compile_plan(plan,fragments={'host_output':fragment})
    fragment['canvas']['width']=20
    with pytest.raises(ValueError,match='SHA'):compile_plan(plan,fragments={'host_output':fragment})


@pytest.mark.parametrize('kind',['repeats','mirrors','magnifies','aligned_with'])
def test_unsupported_execute_is_explicit_needs_input(kind):
    plan=request();plan['relations'][0]['type']=kind
    with pytest.raises(ValueError,match='needs_input:'):compile_plan(plan)
    plan['relations'][0]['mode']='semantic'
    _,_,report=compile_plan(plan)
    assert report['semantic_assertions']==['transfer']


def test_reference_unknowns_and_color_meaning_are_not_inferred():
    plan=request();plan['task_type']='reference_reconstruction'
    with pytest.raises(ValueError,match='source SHA'):validate_plan(plan)
    plan['sources']=[{'id':'source','sha256':'a'*64,'size':[100,100],'region':[0,0,100,100]}]
    plan['color_semantics']=[{'target':{'object_id':'sample','part':'body'},'field':'fill','value':'#ff0000','meaning':'treatment','locked':True}]
    with pytest.raises(ValueError,match='protected scientific color'):compile_plan(plan)
    plan['modules'][0]['objects'][0]['style_bindings']={'body.fill':'#ff0000'}
    compile_plan(plan)


def test_asset_source_attribution_survives_compile_and_project(tmp_path):
    from scientific_figure.registry import resolve
    entry=resolve('twemoji.lemon',version='14.0.2')
    plan=request();plan['relations']=[]
    for spec in plan['modules'][0]['objects']:
        spec['construction']={'kind':'asset','id':'twemoji.lemon','version':'14.0.2','sha256':entry['asset']['sha256']}
    scene,extras,_=compile_plan(plan)
    mapping=extras['generation.json']['input']['inputs']['object_map']
    assert mapping['sample']['source_provenance']['registry_asset']['entry']['license']['identifier']=='CC-BY-4.0'
    write_project(scene,tmp_path/'project',scale=1,extras=extras)
    assert load_project(tmp_path/'project')==scene


def test_baked_dependency_still_conflicts_on_user_text_edit(tmp_path):
    scene,extras,_=compile_plan(request());root=tmp_path/'project';write_project(scene,root,scale=1,extras=extras)
    label=extras['generation.json']['input']['inputs']['object_map']['sample']['part_ids']['label']
    node(scene,label)['geometry']['text']='User text requiring new layout';scene['revision']=1
    write_project(scene,root,scale=1,expected_revision=0)
    candidate,e,_=template_candidate(root,{'sample':{'params':{'width':190}}})
    write_candidate(candidate,e,tmp_path/'candidate')
    assert diff_generation(root,tmp_path/'candidate',tmp_path/'diff')['status']=='conflicts'


def test_direct_fragment_namespace_and_required_editable_part():
    plan=request();plan['relations']=[]
    for spec in plan['modules'][0]['objects']:
        spec['construction']={'kind':'direct','objects':[{'id':'body','kind':'ellipse','geometry':{'cx':10,'cy':10,'rx':10,'ry':5}}]}
        spec['editable_parts']=['body']
    scene,extras,_=compile_plan(plan)
    entries=extras['generation.json']['input']['inputs']['object_map']
    assert entries['sample']['part_ids']['body']!=entries['result']['part_ids']['body']
    plan['modules'][0]['objects'][0]['editable_parts']=['unknown']
    with pytest.raises(ValueError,match='editable parts'):compile_plan(plan)

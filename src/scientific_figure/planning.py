"""Thin deterministic intent compiler. The host supplies science and geometry.

No model, source execution or secondary editable geometry store. FigurePlan is an
inert input snapshot bound through generation inputs and the project receipt.
"""
from __future__ import annotations
import copy
import hashlib
from pathlib import Path

from .builder import SceneBuilder
from .context import canonical_hash, _layout_matches
from .geometry import Geometry, IDENTITY
from .primitives import namespaced_id
from .scene import REF, schema_validate, validate


RELATIONS = {'contains', 'connects', 'points_to', 'overlaps', 'behind', 'in_front_of',
             'repeats', 'mirrors', 'aligned_with', 'magnifies', 'belongs_to', 'occludes'}


def validate_plan(plan):
    schema_validate(plan, 'figure-plan')
    labels = [m['id'] for m in plan['modules']]
    specs = [o for m in plan['modules'] for o in m['objects']]
    labels += [o['id'] for o in specs] + [r['id'] for r in plan['relations']]
    if len(labels) != len(set(labels)) or any(label.startswith('sf-') for label in labels):
        raise ValueError('duplicate/reserved plan ID')
    sources = {s['id'] for s in plan.get('sources', [])}
    if len(sources) != len(plan.get('sources', [])): raise ValueError('duplicate source ID')
    if plan['task_type'] == 'reference_reconstruction' and not sources:
        raise ValueError('needs_input: reference reconstruction requires source SHA')
    for source in plan.get('sources', []):
        if 'region' in source and min(source['region'][2:]) <= 0: raise ValueError('invalid source ROI')
    for entry in plan['modules'] + specs:
        if 'source_id' in entry and entry['source_id'] not in sources: raise ValueError('unknown plan source')
    ids = {o['id'] for o in specs}
    endpoints = [end for r in plan['relations'] for end in (r['source'], r['target'])]
    endpoints += [x['target'] for key in ('token_bindings', 'color_semantics') for x in plan.get(key, [])]
    if any(end['object_id'] not in ids for end in endpoints): raise ValueError('unknown plan object reference')
    if set(plan['review_spec'].get('required_objects', [])) - ids: raise ValueError('unknown required object')
    if 'style_profile' in plan:
        from .style_profile import validate_profile
        validate_profile(plan['style_profile'])
    return {'status': 'valid', 'objects': len(specs), 'modules': len(plan['modules']),
            'semantic_understanding': 'host_supplied', 'scientific_review': 'not_executed'}


def _import_fragment(builder, wrapper, fragment):
    from .composition import _semantic
    validate(fragment)
    objects = {o['id']: namespaced_id(wrapper, o['id']) for o in fragment['objects']}
    resources = {r['id']: namespaced_id(wrapper, r['id'], 'r') for r in fragment.get('resources', [])}
    nodes, paints, cuts = [], [], []
    for original in fragment['objects']:
        node = copy.deepcopy(original); node['id'] = objects[original['id']]
        node['parent'] = objects[original['parent']] if original.get('parent') else wrapper
        if node['kind'] == 'connector':
            for end in ('from', 'to'): node['geometry'][end] = objects[node['geometry'][end]]
        for key in ('fill', 'stroke'):
            match = REF.fullmatch(node.get('style', {}).get(key, ''))
            if match: node['style'][key] = 'url(#' + resources[match[1]] + ')'
        for field in ('clip', 'mask'):
            if field in node: node[field] = resources[node[field]]
        if 'semantic' in node: node['semantic'] = _semantic(node['semantic'], objects, resources)
        nodes.append(node)
    for original in fragment.get('resources', []):
        resource = copy.deepcopy(original); resource['id'] = resources[resource['id']]; paints.append(resource)
    for original in fragment.get('occlusions', []):
        cut = copy.deepcopy(original); cut['id'] = namespaced_id(wrapper, cut['id'], 'x')
        for key in ('over', 'under'): cut[key] = objects[cut[key]]
        cut['frame'] = objects[cut['frame']] if cut['frame'] else wrapper; cuts.append(cut)
    builder.add_many(nodes, resources=paints, occlusions=cuts)
    return {'root_id': wrapper, 'part_ids': objects, 'resource_ids': list(resources.values()), 'dependencies': [],
            'source_provenance': copy.deepcopy(fragment.get('provenance', {}))}


def _endpoint(endpoint, mappings):
    entry = mappings[endpoint['object_id']]
    if 'part' not in endpoint: return entry['root_id']
    if endpoint['part'] not in entry['part_ids']: raise ValueError('needs_input: unknown requested part ' + endpoint['part'])
    return entry['part_ids'][endpoint['part']]


def compile_plan(plan, *, fragments=None, registry=None, planning_context=None):
    """Return (bound Scene, project extras, lowering report), never run source.

    Program fragments are host-produced Scene dictionaries supplied explicitly;
    their canonical digest must match the request. Asset access is local only.
    """
    from . import __version__
    from .generation import attach_generation, generation_input
    from .recipes import build as recipe_build
    validate_plan(plan)
    if planning_context is not None:
        from .planning_tools import validate_context
        validate_context(plan, planning_context)
    fragments = fragments or {}
    canvas = plan['canvas']
    builder = SceneBuilder(canvas['width'], canvas['height'], background=canvas.get('background', 'none'), schema_version='0.3')
    mappings, instances, dependencies, lowered = {}, {}, [], []
    for module in plan['modules']:
        module_id = namespaced_id(plan['id'], module['id'])
        builder.group(module_id, transform=module.get('transform', IDENTITY))
        for spec in module['objects']:
            oid, construction = spec['id'], spec['construction']
            builder.group(oid, parent=module_id, transform=spec.get('transform', IDENTITY),
                          semantic={'role': spec['role']}, anchors=spec.get('ports', {}))
            kind = construction['kind']
            if kind == 'unresolved': raise ValueError('needs_input: ' + construction['reason'])
            if kind == 'recipe':
                result = recipe_build(builder, recipe_id=construction['id'], version=construction['version'],
                    instance_id=oid, params=construction['params'], parent=oid,
                    style_bindings=spec.get('style_bindings'))
                for name, port in result.get('ports', {}).items():
                    if name not in spec.get('ports', {}): builder.port(oid, name, port['position'])
                instances[oid] = {'recipe_id': construction['id'], 'version': construction['version'],
                                 'params': copy.deepcopy(result['params']), 'root_id': oid,
                                 'part_ids': copy.deepcopy(result['part_ids']),
                                 'style_bindings': copy.deepcopy(spec.get('style_bindings', {}))}
                dependencies.extend(result.get('dependencies', []))
            else:
                if spec.get('style_bindings'): raise ValueError('needs_input: direct/asset/program styles must be explicit Scene values')
                if kind == 'direct':
                    fragment = {'schema_version': '0.3', 'revision': 0, 'canvas': canvas,
                                'objects': construction['objects'], 'resources': construction.get('resources', [])}
                    if construction.get('occlusions'): fragment['occlusions'] = construction['occlusions']
                elif kind == 'program':
                    fragment = fragments.get(construction['candidate'])
                    if fragment is None: raise ValueError('needs_input: host program candidate not supplied')
                    if canonical_hash(fragment) != construction['sha256']: raise ValueError('program candidate SHA mismatch')
                else:
                    from .registry import resolve, load_asset
                    entry = resolve(construction['id'], version=construction['version'], registry=registry,
                                    expected_sha256=construction['sha256'])
                    fragment = load_asset(entry, registry=registry)
                result = _import_fragment(builder, oid, fragment)
            mappings[oid] = {**result, 'root_id': oid}
            missing = set(spec.get('editable_parts', [])) - set(result['part_ids'])
            if missing: raise ValueError('needs_input: required editable parts missing: ' + ', '.join(sorted(missing)))
    pending = []
    for relation in plan['relations']:
        mode, kind = relation['mode'], relation['type']
        if mode != 'execute':
            lowered.append({'id': relation['id'], 'type': kind, 'mode': mode, 'status': 'host_assertion' if mode == 'semantic' else 'check'})
            continue
        if relation.get('certainty') == 'uncertain': raise ValueError('needs_input: uncertain relation cannot execute')
        source, target = _endpoint(relation['source'], mappings), _endpoint(relation['target'], mappings)
        params = relation.get('params', {})
        if kind in {'connects', 'points_to'}:
            if params: raise ValueError('unsupported connector parameters')
            builder.add(relation['id'], 'connector', {'from': source, 'to': target,
                'from_anchor': relation['source'].get('anchor', 'right'), 'to_anchor': relation['target'].get('anchor', 'left')},
                style={'stroke': '#34556b', 'stroke_width': 2, 'arrow_end': kind == 'points_to', **relation.get('style', {})})
        elif kind == 'occludes':
            if set(params) != {'frame', 'region', 'gap'}: raise ValueError('occludes requires explicit frame, region, gap')
            frame = params['frame']
            if frame is not None:
                if frame not in mappings: raise ValueError('unknown occlusion frame')
                frame = mappings[frame]['root_id']
            builder.occlusion(relation['id'], source, target, frame=frame, region=params['region'], gap=params['gap'])
        elif kind in {'behind', 'in_front_of', 'contains', 'belongs_to'}:
            pending.append((relation, source, target))
        else:
            raise ValueError('needs_input: relation requires explicit construction or semantic/check mode: ' + kind)
        lowered.append({'id': relation['id'], 'type': kind, 'mode': mode, 'status': 'lowered'})
    scene = builder.build()
    scene['canvas'] = copy.deepcopy(canvas)
    nodes = {o['id']: o for o in scene['objects']}
    for relation, source, target in pending:
        kind, params = relation['type'], relation.get('params', {})
        if kind in {'contains', 'belongs_to'}:
            if params != {'reparent': True}: raise ValueError('reparent relation must explicitly set reparent=true')
            parent, child = (source, target) if kind == 'contains' else (target, source)
            if nodes[parent]['kind'] != 'group': raise ValueError('relation parent must be a group')
            nodes[child]['parent'] = parent
        else:
            if params: raise ValueError('paint order accepts no parameters')
            if nodes[source].get('parent') != nodes[target].get('parent'): raise ValueError('needs_input: paint order requires sibling objects')
            before, after = (source, target) if kind == 'behind' else (target, source)
            item = nodes[before]; scene['objects'].remove(item)
            scene['objects'].insert(scene['objects'].index(nodes[after]), item)
    bindings = [{'object_id': _endpoint(b['target'], mappings), 'field': b['field'], 'token': b['token']} for b in plan.get('token_bindings', [])]
    if bindings:
        from .style_profile import resolve_bindings
        resolve_bindings(scene, plan.get('style_profile'), bindings)
    geometry = Geometry(scene)
    for lock in plan.get('color_semantics', []):
        if lock['locked'] and geometry.style(_endpoint(lock['target'], mappings)).get(lock['field'], '#000000' if lock['field'] == 'fill' else 'none') != lock['value']:
            raise ValueError('needs_input: protected scientific color conflicts with construction/style')
    validate(scene)
    # Only construction-time dependencies are baked. Paint references, parents
    # and occlusions are live Scene references and must not claim stale paths.
    resource_ids = {r['id'] for r in scene.get('resources', [])}
    baked = {}
    for dependency in dependencies:
        if dependency['kind'] in {'paint_resource', 'parent', 'explicit_occlusion'}:
            continue
        baked.setdefault(dependency['target'], set()).update(dependency['sources'])
    generation_dependencies = [{'object_id': oid, 'depends_on': [
        {'type': 'resource' if ref in resource_ids else 'object', 'id': ref}
        for ref in sorted(refs)]} for oid, refs in sorted(baked.items())]
    frames = {}
    for entry in generation_dependencies:
        for oid, instance in instances.items():
            parts = set(instance['part_ids'].values()) - {instance['part_ids']['root']}
            if entry['object_id'] in parts and all(ref['type'] != 'object' or ref['id'] in parts for ref in entry['depends_on']):
                frames[entry['object_id']] = instance['part_ids']['root']
                break
    inputs = {'figure_plan': copy.deepcopy(plan), 'figure_plan_sha256': canonical_hash(plan),
              'dependency_frames': frames,
              'object_map': mappings, 'recipe_instances': instances, 'lowering': lowered,
              'fragments': copy.deepcopy(fragments), 'recipe_library_sha256': recipe_library_sha256()}
    if planning_context is not None:
        inputs['planning_context'] = copy.deepcopy(planning_context)
    record = generation_input(scene, language='python', source='from scientific_figure.planning import compile_plan\nscene, extras, report = compile_plan(params["request"], fragments=params.get("fragments"))\n',
        params={'request': copy.deepcopy(plan), 'fragments': copy.deepcopy(fragments)},
        backend={'name': 'figure-plan', 'version': __version__}, inputs=inputs, dependencies=generation_dependencies)
    bound, extras = attach_generation(scene, record)
    extras['figure-plan.json'] = copy.deepcopy(plan)
    report = plan_review(bound, plan, mappings, planning_context=planning_context, baseline=extras['generation.json']['baseline'])
    report['lowering'] = lowered
    return bound, extras, report


def recipe_library_sha256():
    root = Path(__file__).parent/'recipes'
    digest = hashlib.sha256()
    for path in sorted(root.glob('*.py')):
        digest.update(path.name.encode()); digest.update(path.read_bytes())
    return digest.hexdigest()


def validate_planning(scene, documents):
    """Check inert intent snapshots against their generation binding, not render state."""
    plan = documents.get('figure-plan.json')
    inputs = documents.get('generation.json', {}).get('input', {}).get('inputs', {})
    if plan is None:
        if 'figure_plan_sha256' in inputs or 'planning_context' in inputs: raise ValueError('missing figure-plan.json')
        return
    validate_plan(plan)
    if 'planning_context' in inputs:
        from .planning_tools import validate_context
        validate_context(plan, inputs['planning_context'])
    if canonical_hash(plan) != inputs.get('figure_plan_sha256') or inputs.get('figure_plan') != plan:
        raise ValueError('figure plan input binding mismatch')
    baseline_ids = {o['id'] for o in documents['generation.json']['baseline']['objects']}
    mappings = inputs.get('object_map', {})
    expected = {o['id'] for m in plan['modules'] for o in m['objects']}
    if set(mappings) != expected: raise ValueError('figure plan mapping closure mismatch')
    for entry in mappings.values():
        if entry['root_id'] not in baseline_ids or set(entry['part_ids'].values()) - baseline_ids:
            raise ValueError('figure plan part refs missing from pure baseline')
    # Current user deletions remain legal; review reports missing required parts.


def plan_review(scene, plan, mappings, *, planning_context=None, baseline=None):
    geometry = Geometry(scene); checks = []
    trace = []
    old = {o['id']: o for o in baseline['objects']} if baseline else {}
    specs = {o['id']: o for m in plan['modules'] for o in m['objects']}
    for oid, spec in specs.items():
        entry = mappings[oid]
        parts = entry['part_ids']
        for part in spec.get('editable_parts', []):
            current_id = parts.get(part)
            checks.append({'kind': 'required_part', 'plan_object_id': oid, 'part': part,
                'object_id': current_id, 'status': 'passed' if current_id in geometry.nodes and geometry.visible_bounds(current_id) is not None else 'failed'})
        ids = set(parts.values()) | {entry['root_id']}
        changed = sorted(i for i in ids if i in old and geometry.nodes.get(i) != old[i])
        texts = [{'object_id': i, 'text': geometry.nodes[i]['geometry']['text'],
                  'differs_from_input': i in old and old[i].get('geometry', {}).get('text') != geometry.nodes[i]['geometry']['text']}
                 for i in sorted(ids) if i in geometry.nodes and geometry.nodes[i]['kind'] == 'text']
        trace.append({'plan_object_id': oid, 'construction_kind': spec['construction']['kind'],
            'root_id': entry['root_id'], 'part_ids': copy.deepcopy(parts),
            'missing_current_ids': sorted(ids - set(geometry.nodes)), 'changed_from_baseline': changed,
            'text_inventory': texts, 'must_preserve': spec.get('must_preserve', []),
            'unknown': spec.get('unknown', []), 'visual_structure': 'host_required'})
    for oid in plan['review_spec'].get('required_objects', []):
        root = mappings[oid]['root_id']; checks.append({'kind': 'required_object', 'object_id': root, 'status': 'passed' if root in geometry.nodes and geometry.visible_bounds(root) is not None else 'failed'})
    for lock in plan.get('color_semantics', []):
        if not lock['locked']: continue
        oid = _endpoint(lock['target'], mappings)
        actual = geometry.style(oid).get(lock['field'], '#000000' if lock['field'] == 'fill' else 'none') if oid in geometry.nodes else None
        checks.append({'kind': 'protected_color', 'object_id': oid, 'meaning': lock['meaning'], 'status': 'passed' if actual == lock['value'] else 'failed'})
    for relation in plan['relations']:
        if relation['mode'] != 'check': continue
        source, target = _endpoint(relation['source'], mappings), _endpoint(relation['target'], mappings)
        params = relation.get('params', {})
        if relation['type'] in {'aligned_with', 'contains'}:
            kind = params.get('relation', 'contains' if relation['type'] == 'contains' else 'aligned_left')
            if kind not in {'aligned_left', 'aligned_top', 'same_width', 'same_height', 'contains', 'left_of', 'above'}: raise ValueError('unsupported layout check')
            tolerance = params.get('tolerance', 1)
            if type(tolerance) not in {int, float} or not 0 <= tolerance <= 1000: raise ValueError('invalid layout tolerance')
            ok = source in geometry.nodes and target in geometry.nodes and _layout_matches({'relation': kind, 'object_ids': [source, target], 'tolerance': tolerance}, geometry)
            status = 'passed' if ok else 'failed'
        else: status = 'needs_review'
        checks.append({'kind': 'relation', 'id': relation['id'], 'status': status})
    return {'status': 'failed' if any(c['status'] == 'failed' for c in checks) else 'needs_review',
            'checks': checks, 'trace': trace, 'unresolved': plan['review_spec'].get('unknown', []),
            'planning_context': copy.deepcopy(planning_context),
            'confirmation_scope': 'construction_input_snapshot_only',
            'visual_review': 'host_required', 'scientific_review': 'not_executed',
            'semantic_assertions': [r['id'] for r in plan['relations'] if r['mode'] == 'semantic'],
            'source_verification': 'host_declared_unless_prepared_consumption_is_bound'}


def write_candidate(scene, extras, directory):
    """Publish exactly the two generation source files, never an executable entry."""
    from .project import json_text, project_lock
    import shutil
    import tempfile
    directory = Path(directory).resolve(); directory.parent.mkdir(parents=True, exist_ok=True)
    if directory.exists(): raise ValueError('candidate output already exists')
    pure = copy.deepcopy(extras['generation.json']['baseline'])
    record = extras['generation.json']['input']
    stage = Path(tempfile.mkdtemp(prefix='.candidate-', dir=directory.parent))
    try:
        (stage/'scene.json').write_text(json_text(pure), encoding='utf-8')
        (stage/'generation-input.json').write_text(json_text(record), encoding='utf-8')
        with project_lock(directory):
            if directory.exists(): raise ValueError('candidate output already exists')
            stage.rename(directory)
    finally:
        if stage.exists(): shutil.rmtree(stage)
    return {'status': 'success', 'candidate': str(directory), 'objects': len(pure['objects'])}


def template_candidate(project, updates):
    """Generate selected instance parameter changes; never accept or execute source.

    Current only supports pinned installed Recipe versions. New versions must be
    genuinely registered; renames/part migrations require an explicit new plan.
    """
    from .project import load_project, project_extras
    scene = load_project(project); documents = project_extras(project)
    validate_planning(scene, documents)
    plan = copy.deepcopy(documents.get('figure-plan.json'))
    if plan is None: raise ValueError('template update requires a compiled FigurePlan project')
    if not isinstance(updates, dict) or not updates: raise ValueError('choose a nonempty instance update mapping')
    specs = {o['id']: o for m in plan['modules'] for o in m['objects']}
    if set(updates) - set(specs): raise ValueError('unknown selected template instance')
    for oid, update in updates.items():
        if not isinstance(update, dict) or set(update) - {'params', 'version', 'transform', 'style_bindings'}: raise ValueError('unsupported instance update fields')
        spec = specs[oid]; construction = spec['construction']
        if construction['kind'] != 'recipe': raise ValueError('selected instance is not a Recipe')
        if 'params' in update:
            if not isinstance(update['params'], dict): raise ValueError('params must be an object')
            construction['params'].update(copy.deepcopy(update['params']))
        if 'version' in update: construction['version'] = update['version']
        for key in ('transform', 'style_bindings'):
            if key in update: spec[key] = copy.deepcopy(update[key])
    inputs = documents['generation.json']['input']['inputs']
    if inputs.get('recipe_library_sha256') != recipe_library_sha256():
        raise ValueError('recipe implementation changed; explicit version/migration review required')
    context = copy.deepcopy(inputs.get('planning_context'))
    if context is not None:
        context['plan_sha256'] = canonical_hash(plan)
        for oid in updates:
            decision = context.get('objects', {}).get(oid)
            if decision is not None:
                decision['construction_sha256'] = canonical_hash(specs[oid]['construction'])
                decision['confirmed'] = False
                decision['reason'] += '\nSelected-instance update; renewed host confirmation required.'
    return compile_plan(plan, fragments=inputs.get('fragments'), planning_context=context)

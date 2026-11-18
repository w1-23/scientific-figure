"""Thin deterministic intent compiler. The host supplies science and geometry.

No model, source execution or secondary editable geometry store. FigurePlan is an
inert input snapshot bound through generation inputs and the project receipt.
"""
from __future__ import annotations
import copy
import hashlib
import math
import re
from pathlib import Path

from .builder import SceneBuilder
from .context import canonical_hash, _layout_matches
from .coords import frame_matrix
from .geometry import Geometry, IDENTITY, inverse, multiply
from .primitives import namespaced_id
from .scene import REF, schema_validate, validate


RELATIONS = {'contains', 'connects', 'points_to', 'overlaps', 'behind', 'in_front_of',
             'repeats', 'mirrors', 'aligned_with', 'magnifies', 'belongs_to', 'occludes'}

# mode -> relation types the compiler can actually execute or validate. Anything
# else must be declared semantic; an explicit error beats a silent no-op.
EXECUTABLE = {'contains', 'connects', 'points_to', 'occludes', 'behind', 'in_front_of',
              'repeats', 'mirrors', 'belongs_to'}
CHECKABLE = {'aligned_with', 'overlaps', 'magnifies', 'contains'}


def relation_support(kind, mode):
    if mode == 'semantic':
        return 'semantic_only'
    if mode == 'check':
        return 'validation_only'
    if kind in EXECUTABLE:
        return 'executable'
    raise ValueError('needs_input: relation has no execute lowering; use check or semantic: ' + kind)


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
    for module in plan['modules']:
        if 'local_frame' in module and 'transform' in module:
            raise ValueError('module takes either local_frame or transform, not both')
        if 'local_frame' in module:
            frame_matrix(module['local_frame'])
        if 'source_region' in module:
            _region_in_source(module.get('source_id'), module['source_region'], plan)
    for relation in plan['relations']:
        relation_support(relation['type'], relation['mode'])
        if relation['mode'] == 'execute' and relation.get('params', {}).get('frame') is not None:
            if relation['type'] != 'occludes':
                raise ValueError('only occlusion takes an explicit frame')
    if plan['task_type'] == 'reference_reconstruction':
        for spec in specs:
            if 'fidelity' in spec and 'source_region' not in spec:
                raise ValueError('fidelity constraint requires an explicit source_region')
            if 'source_region' in spec:
                _region_in_source(spec.get('source_id'), spec['source_region'], plan)
    if 'style_profile' in plan:
        from .style_profile import validate_profile
        validate_profile(plan['style_profile'])
    return {'status': 'valid', 'objects': len(specs), 'modules': len(plan['modules']),
            'semantic_understanding': 'host_supplied', 'scientific_review': 'not_executed'}


def _region_in_source(source_id, region, plan):
    """Regions are source pixel boundaries; they must fit a declared source size."""
    if (not isinstance(region, (list, tuple)) or len(region) != 4
            or any(type(v) not in {int, float} for v in region)):
        raise ValueError('source region must be four numbers')
    if min(region[2:]) <= 0:
        raise ValueError('source region must have positive extent')
    sources = {s['id']: s for s in plan.get('sources', [])}
    source = sources.get(source_id) if source_id else (next(iter(sources.values())) if len(sources) == 1 else None)
    if source is not None and 'size' in source:
        x, y, w, h = region
        if x < 0 or y < 0 or x + w > source['size'][0] or y + h > source['size'][1]:
            raise ValueError('source region outside declared source size')
    return region


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


def _mirror(builder, relation, source, target):
    """Place the target mirrored about the source's bounding-box axis."""
    params = relation.get('params', {})
    if set(params) - {'axis'}:
        raise ValueError('mirrors accepts an axis parameter only')
    axis = params.get('axis', 'vertical')
    if axis not in {'vertical', 'horizontal'}:
        raise ValueError('mirror axis must be vertical or horizontal')
    scene = builder.build()
    geometry = Geometry(scene)
    box = geometry.bounds(source)
    if box is None:
        raise ValueError('mirror source has no visible bounds')
    x0, y0, x1, y1 = box
    matrix = [-1, 0, 0, 1, x0 + x1, 0] if axis == 'vertical' else [1, 0, 0, -1, 0, y0 + y1]
    nodes = {o['id']: o for o in scene['objects']}
    parent = nodes[target].get('parent')
    parent_world = geometry.world(parent) if parent else IDENTITY
    builder.transform(target, multiply(inverse(parent_world), multiply(matrix, geometry.world(target))))


def _repeat(builder, relation, source, target):
    """Clone the source subtree as explicit, independently editable instances."""
    params = relation.get('params', {})
    if set(params) != {'instances'}:
        raise ValueError('repeats requires explicit instances and nothing else')
    instances = params['instances']
    if not isinstance(instances, list) or not 1 <= len(instances) <= 64:
        raise ValueError('repeats requires 1..64 explicit instances')
    scene = builder.build()
    nodes = {o['id']: o for o in scene['objects']}
    if target not in nodes or nodes[target]['kind'] != 'group':
        raise ValueError('repeats target must be a group')
    geometry = Geometry(scene)
    subtree, stack = set(), [source]
    while stack:
        node = stack.pop()
        if node in subtree:
            continue
        subtree.add(node)
        stack.extend(geometry.children.get(node, []))
    objects = [copy.deepcopy(o) for o in scene['objects'] if o['id'] in subtree]
    for obj in objects:
        if obj['id'] == source:
            obj.pop('parent', None)
    cuts = [copy.deepcopy(c) for c in scene.get('occlusions', [])
            if {c['over'], c['under']} <= subtree
            and (c['frame'] is None or c['frame'] in subtree)]
    fragment = {'schema_version': scene['schema_version'], 'revision': 0,
                'canvas': copy.deepcopy(scene['canvas']), 'objects': objects,
                'resources': copy.deepcopy(scene.get('resources', [])),
                'occlusions': cuts}
    for index, entry in enumerate(instances, 1):
        if not isinstance(entry, dict) or set(entry) - {'suffix', 'transform'}:
            raise ValueError('repeat instance accepts suffix and transform only')
        suffix = entry.get('suffix', str(index))
        if not isinstance(suffix, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,24}', suffix):
            raise ValueError('repeat instance suffix must be 1..24 safe characters')
        wrapper = f'{target}.{suffix}'
        if wrapper in nodes:
            raise ValueError('duplicate repeat instance ID')
        builder.group(wrapper, parent=target)
        _import_fragment(builder, wrapper, fragment)
        if entry.get('transform') is not None:
            builder.transform(wrapper, entry['transform'])


def _endpoint(endpoint, mappings):
    entry = mappings[endpoint['object_id']]
    if 'part' not in endpoint: return entry['root_id']
    if endpoint['part'] not in entry['part_ids']: raise ValueError('needs_input: unknown requested part ' + endpoint['part'])
    return entry['part_ids'][endpoint['part']]


def validate_evidence_bindings(plan, pack):
    """Resolve every declared ``evidence_ids`` entry against one constraint pack.

    Plan ``source_region`` values are oriented-source pixels; pack contour,
    region, hole and repetition boxes are ROI-local, so the pack ROI origin is
    subtracted before comparison. A cited ID must exist, and when the object also
    declares a source region at least one cited box must intersect it. Evidence
    stays geometry: a passing binding says nothing about scientific identity.
    """
    from .constraints import evidence_boxes
    if not isinstance(pack, dict) or pack.get("kind") != "constraint_pack":
        raise ValueError("expected a constraint_pack document")
    boxes = evidence_boxes(pack)
    roi = pack["source"]["roi"]
    ox, oy, rw, rh = roi
    report = []
    for module in plan["modules"]:
        for spec in module["objects"]:
            cited = list(spec.get("evidence_ids", []))
            region = spec.get("source_region")
            if not cited and region is None:
                continue
            unknown = sorted(set(cited) - set(boxes))
            if unknown:
                raise ValueError("needs_input: unknown evidence IDs for " + spec["id"]
                                 + ": " + ", ".join(unknown))
            local = None
            if region is not None:
                local = (region[0] - ox, region[1] - oy, region[0] - ox + region[2],
                         region[1] - oy + region[3])
                if local[2] <= 0 or local[3] <= 0 or local[0] >= rw or local[1] >= rh:
                    raise ValueError("needs_input: source region lies outside the constraint pack ROI: " + spec["id"])
            hits = []
            if cited and local is not None:
                for key in cited:
                    box = boxes[key]
                    if _intersects(list(local), list(box)):
                        hits.append(key)
                if not hits:
                    raise ValueError("needs_input: cited evidence lies outside the declared source region: " + spec["id"])
            report.append({"object_id": spec["id"], "evidence_ids": cited,
                           "source_region": region, "intersecting": hits})
    return report


def compile_plan(plan, *, fragments=None, registry=None, planning_context=None,
                 evidence_pack=None):
    """Return (bound Scene, project extras, lowering report), never run source.

    Program fragments are host-produced Scene dictionaries supplied explicitly;
    their canonical digest must match the request. Asset access is local only.
    """
    from . import __version__
    from .generation import attach_generation, generation_input
    from .recipes import build as recipe_build
    validate_plan(plan)
    evidence_report = None
    if evidence_pack is not None:
        evidence_report = validate_evidence_bindings(plan, evidence_pack)
    if planning_context is not None:
        from .planning_tools import validate_context
        validate_context(plan, planning_context)
    fragments = fragments or {}
    canvas = plan['canvas']
    builder = SceneBuilder(canvas['width'], canvas['height'], background=canvas.get('background', 'none'), schema_version='0.3')
    mappings, instances, dependencies, lowered = {}, {}, [], []
    specs = [o for m in plan['modules'] for o in m['objects']]
    module_bindings = [{'module_id': m['id'], 'source_id': m.get('source_id'),
                        'source_region': m.get('source_region') or m.get('region')}
                       for m in plan['modules']]
    for module in plan['modules']:
        module_id = namespaced_id(plan['id'], module['id'])
        module_transform = module.get('transform')
        if module_transform is None and 'local_frame' in module:
            module_transform = frame_matrix(module['local_frame'])
        builder.group(module_id, transform=module_transform or IDENTITY)
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
        support = relation_support(kind, mode)
        if mode != 'execute':
            lowered.append({'id': relation['id'], 'type': kind, 'mode': mode, 'support': support,
                            'status': 'host_assertion' if mode == 'semantic' else 'check'})
            continue
        if relation.get('certainty') == 'uncertain': raise ValueError('needs_input: uncertain relation cannot execute')
        source, target = _endpoint(relation['source'], mappings), _endpoint(relation['target'], mappings)
        params = relation.get('params', {})
        if kind in {'connects', 'points_to'}:
            if params: raise ValueError('unsupported connector parameters')
            builder.add(relation['id'], 'connector', {'from': source, 'to': target,
                'from_anchor': relation['source'].get('anchor', 'right'), 'to_anchor': relation['target'].get('anchor', 'left')},
                style={'stroke': '#34556b', 'stroke_width': 2, 'arrow_end': kind == 'points_to', **relation.get('style', {})})
            status = 'lowered'
        elif kind == 'occludes':
            if set(params) != {'frame', 'region', 'gap'}: raise ValueError('occludes requires explicit frame, region, gap')
            frame = params['frame']
            if frame is not None:
                if frame not in mappings: raise ValueError('unknown occlusion frame')
                frame = mappings[frame]['root_id']
            builder.occlusion(relation['id'], source, target, frame=frame, region=params['region'], gap=params['gap'])
            status = 'lowered'
        elif kind == 'mirrors':
            _mirror(builder, relation, source, target)
            status = 'lowered'
        elif kind == 'repeats':
            _repeat(builder, relation, source, target)
            status = 'lowered'
        elif kind in {'behind', 'in_front_of', 'contains', 'belongs_to'}:
            pending.append((relation, source, target))
            status = 'deferred_paint_or_parent'
        else:
            raise ValueError('needs_input: relation requires explicit construction or semantic/check mode: ' + kind)
        lowered.append({'id': relation['id'], 'type': kind, 'mode': mode,
                        'support': support, 'status': status})
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
              'fidelity_bindings': [
                  {'object_id': spec['id'], 'source_id': spec.get('source_id'),
                   'source_region': spec.get('source_region'),
                   'evidence_ids': list(spec.get('evidence_ids', [])),
                   'fidelity': copy.deepcopy(spec.get('fidelity')),
                   'construction_strategy': spec.get('construction_strategy')} for spec in specs],
              'module_bindings': module_bindings,
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
    if evidence_report is not None:
        report['evidence_bindings'] = evidence_report
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


def _intersects(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _preserve_target(text, oid, parts, root):
    """Resolve one must-preserve line to a Scene ID, or ``None`` for free text.

    A line that names a part (``cristae``), ``object/part`` (``mitochondrion/cristae``)
    or the object itself is addressable and therefore checkable. Anything else is a
    host judgement: it must stay open instead of counting as satisfied.
    """
    label = text.strip()
    if label in parts:
        return parts[label], 'part'
    if '/' in label:
        head, _, tail = label.partition('/')
        if head == oid and tail in parts:
            return parts[tail], 'object_part'
    if label == oid:
        return root, 'object'
    return None, 'free_text'


def plan_review(scene, plan, mappings, *, planning_context=None, baseline=None,
                constraints_report=None):
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
        for requirement in spec.get('must_preserve', []):
            target, resolution = _preserve_target(requirement, oid, parts, entry['root_id'])
            if target is None:
                checks.append({'kind': 'must_preserve', 'plan_object_id': oid,
                               'requirement': requirement, 'resolution': resolution,
                               'status': 'needs_review',
                               'notes': ['Free text: needs host confirmation or an explicit constraint binding.']})
            else:
                visible = target in geometry.nodes and geometry.visible_bounds(target) is not None
                checks.append({'kind': 'must_preserve', 'plan_object_id': oid,
                               'requirement': requirement, 'resolution': resolution,
                               'object_id': target, 'status': 'passed' if visible else 'failed'})
        changed = sorted(i for i in ids if i in old and geometry.nodes.get(i) != old[i])
        texts = [{'object_id': i, 'text': geometry.nodes[i]['geometry']['text'],
                  'differs_from_input': i in old and old[i].get('geometry', {}).get('text') != geometry.nodes[i]['geometry']['text']}
                 for i in sorted(ids) if i in geometry.nodes and geometry.nodes[i]['kind'] == 'text']
        trace.append({'plan_object_id': oid, 'construction_kind': spec['construction']['kind'],
            'root_id': entry['root_id'], 'part_ids': copy.deepcopy(parts),
            'missing_current_ids': sorted(ids - set(geometry.nodes)), 'changed_from_baseline': changed,
            'text_inventory': texts, 'must_preserve': spec.get('must_preserve', []),
            'unknown': spec.get('unknown', []),
            'source_region': spec.get('source_region'),
            'evidence_ids': list(spec.get('evidence_ids', [])),
            'fidelity': copy.deepcopy(spec.get('fidelity')),
            'construction_strategy': spec.get('construction_strategy'),
            'visual_structure': 'host_required'})
    for oid in plan['review_spec'].get('required_objects', []):
        root = mappings[oid]['root_id']; checks.append({'kind': 'required_object', 'object_id': root, 'status': 'passed' if root in geometry.nodes and geometry.visible_bounds(root) is not None else 'failed'})
    # Figure-level must-preserve lines resolve through the same rule as object
    # lines: an addressable object or object/part is checked; free text stays open.
    addressable = {oid: mappings[oid]['root_id'] for oid in specs}
    for oid, spec in specs.items():
        parts = mappings[oid]['part_ids']
        addressable.update({f'{oid}/{name}': pid for name, pid in parts.items()})
    for requirement in plan['review_spec'].get('must_preserve', []):
        target = addressable.get(requirement.strip())
        if target is None:
            checks.append({'kind': 'must_preserve', 'scope': 'figure', 'requirement': requirement,
                           'resolution': 'free_text', 'status': 'needs_review',
                           'notes': ['Free text: needs host confirmation or an explicit constraint binding.']})
        else:
            visible = target in geometry.nodes and geometry.visible_bounds(target) is not None
            checks.append({'kind': 'must_preserve', 'scope': 'figure', 'requirement': requirement,
                           'resolution': 'addressable', 'object_id': target,
                           'status': 'passed' if visible else 'failed'})
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
        elif relation['type'] == 'overlaps':
            if set(params) - {'min_fraction'}: raise ValueError('overlaps accepts min_fraction only')
            fraction = params.get('min_fraction', 0)
            if type(fraction) not in {int, float} or not 0 <= fraction <= 1: raise ValueError('invalid overlap fraction')
            a, b = geometry.visible_bounds(source), geometry.visible_bounds(target)
            if a is None or b is None:
                status = 'failed'
            else:
                inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
                smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
                status = 'passed' if smaller and inter / smaller >= fraction else 'failed'
        elif relation['type'] == 'magnifies':
            if set(params) - {'min_scale', 'max_scale'}: raise ValueError('magnifies accepts scale bounds only')
            low, high = params.get('min_scale', 1.2), params.get('max_scale', 100)
            a, b = geometry.visible_bounds(source), geometry.visible_bounds(target)
            if a is None or b is None:
                status = 'failed'
            else:
                ratio = math.sqrt(((b[2] - b[0]) * (b[3] - b[1])) / ((a[2] - a[0]) * (a[3] - a[1])))
                inside = b[0] <= a[0] and b[1] <= a[1] and b[2] >= a[2] and b[3] >= a[3]
                status = 'passed' if low <= ratio <= high and inside else 'failed'
        else: status = 'needs_review'
        checks.append({'kind': 'relation', 'id': relation['id'], 'status': status})
    covered = set()
    if constraints_report is not None:
        from .constraints import review_checks
        extra = review_checks(constraints_report)
        checks.extend(extra)
        covered = {check['object_id'] for check in extra}
    for oid, spec in specs.items():
        fidelity = spec.get('fidelity')
        if not fidelity:
            continue
        root = mappings[oid]['root_id']
        if root in covered:
            continue
        checks.append({'kind': 'fidelity', 'object_id': root, 'constraint': 'boundary',
                       'status': 'needs_review',
                       'notes': ['No constraint check covers this object.']})
    return {'status': 'failed' if any(c['status'] == 'failed' for c in checks) else 'needs_review',
            'checks': checks, 'trace': trace, 'unresolved': plan['review_spec'].get('unknown', []),
            'planning_context': copy.deepcopy(planning_context),
            'confirmation_scope': 'construction_input_snapshot_only',
            'visual_review': 'host_required', 'scientific_review': 'not_executed',
            'semantic_assertions': [r['id'] for r in plan['relations'] if r['mode'] == 'semantic'],
            'lowering_support': [{'id': r['id'], 'type': r['type'], 'mode': r['mode'],
                                  'support': relation_support(r['type'], r['mode'])}
                                 for r in plan['relations']],
            'constraints': None if constraints_report is None else {
                'pack_sha256': constraints_report.get('pack_sha256'),
                'summary': constraints_report.get('summary'),
                'overall_status': constraints_report.get('overall', {}).get('status')},
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

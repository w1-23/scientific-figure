"""Small host-driven planning helpers. These functions never infer intent or run source.

Draft request: id, task_type, purpose, canvas; optional sources, modules,
relations, review_spec, audience. Host object declarations may omit construction.
Context 0.1: plan_id/plan_sha256, intent, sources {source_id: sha256}, optional
objects/relations keyed by plan IDs, evidence (self-contained measurement results),
reference_view {sha256, source_id, source_to_view}. Object decisions contain
construction_sha256, reason, strategies, confirmed and optional candidates and
evidence_keys. Confirmation describes this input only, never current approval.
"""
from __future__ import annotations
import copy
from .context import canonical_hash
from .prepared import _fields, _json_object, _sha, _strings

STRATEGIES = {'filled_shape', 'stroke', 'boolean_cut', 'mask', 'occlusion',
              'overlay', 'gradient', 'repeated_instances', 'text'}


def draft_plan(request):
    from .planning import validate_plan
    request = _json_object(request)
    _fields(request, ['id', 'task_type', 'purpose', 'canvas'],
            ['sources', 'modules', 'relations', 'review_spec', 'audience'])
    plan = copy.deepcopy(request)
    plan['schema_version'] = '0.1'
    plan.setdefault('modules', [{'id': 'main', 'objects': []}])
    plan.setdefault('relations', [])
    plan.setdefault('review_spec', {'scientific_review': 'not_executed',
        'text_policy': 'preserve_original' if plan['task_type'] == 'reference_reconstruction' else 'host_authored'})
    for module in plan['modules']:
        for obj in module['objects']:
            obj.setdefault('construction', {'kind': 'unresolved', 'reason': 'Host must select a construction preserving the declared structure.'})
    validate_plan(plan)
    return plan


def context_for(plan, intent):
    """Create an empty input snapshot, without guessing objects or decisions."""
    from .planning import validate_plan
    validate_plan(plan)
    result = {'schema_version': '0.1', 'plan_id': plan['id'],
        'plan_sha256': canonical_hash(plan), 'intent': intent,
        'sources': {s['id']: s['sha256'] for s in plan.get('sources', [])},
        'objects': {}, 'relations': {}, 'evidence': []}
    validate_context(plan, result)
    return result


def validate_context(plan, context):
    context = _json_object(context)
    _fields(context, ['schema_version', 'plan_id', 'plan_sha256', 'intent', 'sources'],
            ['objects', 'relations', 'evidence', 'reference_view'])
    if context['schema_version'] != '0.1' or context['plan_id'] != plan['id'] or context['plan_sha256'] != canonical_hash(plan):
        raise ValueError('planning context plan binding mismatch')
    if not isinstance(context['intent'], str) or not 1 <= len(context['intent']) <= 10000:
        raise ValueError('planning intent must be bounded nonempty text')
    sources = {s['id']: s['sha256'] for s in plan.get('sources', [])}
    if context['sources'] != sources:
        raise ValueError('planning context source binding mismatch')
    specs = {o['id']: o for m in plan['modules'] for o in m['objects']}
    relations = {r['id'] for r in plan['relations']}
    evidence = context.get('evidence', [])
    if not isinstance(evidence, list) or len(evidence) > 64:
        raise ValueError('planning evidence budget exceeded')
    evidence_keys = set()
    for result in evidence:
        from .measurements import validate_measurement
        validate_measurement(result)
        if result['source']['sha256'] not in sources.values():
            raise ValueError('measurement source not in plan')
        key = result['result_sha256']
        if key in evidence_keys: raise ValueError('duplicate planning evidence')
        evidence_keys.add(key)
    for field, ids in [('objects', set(specs)), ('relations', relations)]:
        records = context.get(field, {})
        if not isinstance(records, dict) or set(records) - ids:
            raise ValueError('unknown planning context reference')
        for oid, record in records.items():
            required = ['reason', 'confirmed'] + (['construction_sha256', 'strategies'] if field == 'objects' else [])
            _fields(record, required, ['candidates', 'evidence_keys', 'unknown'])
            if not isinstance(record['reason'], str) or not 1 <= len(record['reason']) <= 10000 or type(record['confirmed']) is not bool:
                raise ValueError('invalid planning decision')
            if field == 'objects':
                if record['construction_sha256'] != canonical_hash(specs[oid]['construction']):
                    raise ValueError('planning construction binding mismatch')
                _strings(record['strategies'], maximum=9, length=40)
                if set(record['strategies']) - STRATEGIES: raise ValueError('unknown construction strategy')
            _strings(record.get('evidence_keys', []), maximum=64, length=64)
            if set(record.get('evidence_keys', [])) - evidence_keys: raise ValueError('unknown measurement reference')
            _strings(record.get('unknown', []))
            candidates = record.get('candidates', [])
            if not isinstance(candidates, list) or len(candidates) > 10: raise ValueError('candidate budget exceeded')
            for candidate in candidates:
                _fields(candidate, ['kind', 'reason'], ['id', 'version'])
                if candidate['kind'] not in {'recipe', 'asset', 'direct', 'program'}: raise ValueError('invalid candidate kind')
                for value in candidate.values():
                    if not isinstance(value, str) or not 1 <= len(value) <= 10000: raise ValueError('invalid candidate description')
    if 'reference_view' in context:
        view = context['reference_view']
        _fields(view, ['sha256', 'source_id', 'source_to_view'])
        _sha(view['sha256'])
        if view['source_id'] not in sources: raise ValueError('unknown reference view source')
        matrix = view['source_to_view']
        import math
        if not isinstance(matrix, list) or len(matrix) != 6 or any(type(v) not in (int, float) or not math.isfinite(v) for v in matrix):
            raise ValueError('invalid reference view mapping')
        from .geometry import inverse
        inverse(matrix)
    return {'status': 'valid', 'confirmation_scope': 'construction_input_snapshot_only',
            'external_sources': {key: 'external_not_packaged' for key in sources},
            'evidence_count': len(evidence)}


def validate_reference_view(documents, directory):
    """Verify the saved reference bytes; never reopen original external paths."""
    context = documents.get('generation.json', {}).get('input', {}).get('inputs', {}).get('planning_context', {})
    view = context.get('reference_view')
    if view is None: return
    from pathlib import Path
    from .project import digest
    path = Path(directory) / 'reference.png'
    if not path.is_file() or digest(path) != view['sha256']:
        raise ValueError('planning saved reference view binding mismatch')


def check_plan(plan, *, context=None, phase='semantic', fragments=None, registry=None):
    from .planning import validate_plan, compile_plan
    validate_plan(plan)
    if phase not in {'semantic', 'construction'}: raise ValueError('invalid planning phase')
    if context is not None: validate_context(plan, context)
    pending = []
    objects = [o for m in plan['modules'] for o in m['objects']]
    if not objects: pending.append({'id': plan['id'], 'kind': 'semantic', 'issue': 'Host must declare objects and necessary structure.'})
    for obj in objects:
        if obj['construction']['kind'] == 'unresolved':
            pending.append({'id': obj['id'], 'kind': 'construction', 'issue': obj['construction']['reason']})
        for unknown in obj.get('unknown', []): pending.append({'id': obj['id'], 'kind': 'unknown', 'issue': unknown})
        if context is not None and not context.get('objects', {}).get(obj['id'], {}).get('confirmed', False):
            pending.append({'id': obj['id'], 'kind': 'confirmation', 'issue': 'Construction input is not host confirmed.'})
    for unknown in plan['review_spec'].get('unknown', []): pending.append({'id': plan['id'], 'kind': 'unknown', 'issue': unknown})
    for relation in plan['relations']:
        if relation.get('certainty') == 'uncertain': pending.append({'id': relation['id'], 'kind': 'relation', 'issue': 'Uncertain relationship requires host interpretation.'})
    report = {'status': 'needs_input' if not objects else 'ready_for_host_review', 'phase': phase,
        'pending': pending, 'semantic_understanding': 'host_supplied', 'visual_review': 'host_required',
        'scientific_review': 'not_executed'}
    if phase == 'construction':
        if any(p['kind'] in {'construction', 'semantic'} for p in pending):
            report['status'] = 'needs_input'
        else:
            try:
                scene, extras, review = compile_plan(plan, fragments=fragments, registry=registry, planning_context=context)
                report.update(status='compilable', objects=len(scene['objects']), plan_review=review)
            except ValueError as exc:
                report.update(status='needs_input', construction_error=str(exc))
    return report


def suggest(request, *, registry=None):
    from .registry import search
    result = search(request, registry=registry)
    result['routes'] = [
        {'kind': 'direct', 'reason': 'Host supplies explicit editable Scene geometry.'},
        {'kind': 'program', 'reason': 'Host explicitly runs drawing code, then supplies a digest-bound Scene fragment; no saved source executes here.'}]
    result['selection'] = 'host_required'
    return result

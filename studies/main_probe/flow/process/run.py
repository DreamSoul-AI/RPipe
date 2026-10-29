"""Aggregate only the paired Runs in the current index, never stale workspaces."""
import hashlib
import json
from rpipe.structure.artifact.io import contained, load_index, record_inputs
from rpipe.structure.artifact.io import atomic_write_text


def run(ctx):
    if ctx.scope != 'study':
        return
    if ctx.state['process'].get('complete') is not True:
        raise RuntimeError('probe requires complete accepted formal Run aggregation')
    expected = {(data, model) for data in ('MNIST', 'CIFAR10')
                for model in ('linear', 'mlp', 'cnn', 'resnet18')}
    from rpipe.structure.artifact.readout import read_run, refs_from_index
    specs = {spec.id: spec.ref for spec in ctx.specs}
    rows, missing, pairs, sources = [], [], [], set()
    index_path = contained(ctx.layout.study_root / 'index.json', ctx.layout.study_root)
    index_hash = hashlib.sha256(index_path.read_bytes()).hexdigest()
    index = load_index(ctx.layout.study_root)
    refs = {ref.id: ref for ref in refs_from_index(ctx.layout.study_root, index)}
    if refs != specs:
        raise ValueError('probe index differs from the explicit current plan')
    inputs = [record_inputs(ref) for ref in refs.values()]
    for experiment in index['experiments']:
        for run in experiment['runs']:
            ref = refs[run['id']]
            result = read_run(ref)
            report = result.get('probe')
            if (result.get('status') != 'succeeded' or result.get('evidence_complete') is not True
                    or result.get('quality') is not True or not isinstance(report, dict)
                    or len(report.get('runs', [])) != 1):
                missing.append(run['id'])
                continue
            row = report['runs'][0]
            pair = (row['data'], row['model'])
            factors = experiment['factors']
            if pair != (factors['data.name'], factors['model.name']) or run['seed'] != 0:
                raise ValueError('probe evidence does not match the current index')
            pairs.append(pair)
            sources.add((report['main_commit'], report['dev_commit'], report['source_manifest_sha256'], report['device'], report.get('torch')))
            rows.append({'run': run['id'], 'data': pair[0], 'model': pair[1],
                         'passed': report.get('selected_passed') is True and report.get('selected_complete') is True
                         and row.get('passed') is True and report.get('source_unchanged') is True
                         and report.get('archive_after', {}).get('unchanged') is True
                         and report.get('main_commit') == '98648f3a5c7db7dccf3ca806410d5b6fdee9484c',
                         'evidence': str(ref.result_path.relative_to(ctx.layout.study_root))})
    complete = not missing and len(pairs) == 8 and set(pairs) == expected and len(sources) == 1
    passed = complete and all(row['passed'] for row in rows)
    body = {'complete': complete, 'passed': passed, 'runs': rows, 'missing_runs': missing,
            'inputs': {'index_sha256': index_hash, 'runs': inputs}}
    if (hashlib.sha256(index_path.read_bytes()).hexdigest() != index_hash
            or [record_inputs(ref) for ref in refs.values()] != inputs):
        raise ValueError('probe inputs changed during Study aggregation')
    atomic_write_text(ctx.layout.study_root / 'summary' / 'PROBE_COMPARISON.json', json.dumps(body, indent=2) + '\n')
    ctx.state['probe'] = body
    if not passed:
        raise RuntimeError('probe Study gate incomplete or failed; see PROBE_COMPARISON.json')

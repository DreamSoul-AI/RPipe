"""Compare explicit observed states before the outer scientific result commits."""

import hashlib
from pathlib import Path


def _model_state(state):
    normalized = {(key[6:] if key.startswith('model.') else key[4:] if key.startswith('net.') else key): value
                  for key, value in state.items()}
    if len(normalized) != len(state):
        raise ValueError('model wrapper prefix normalization would discard a state key')
    return normalized


def compare_observed(raw):
    from rpipe.structure.artifact.comparison import compare_state

    old, current = raw['old_snaps'], raw['current_snaps']
    left, right = [old[step] for step in sorted(old)], [current[step] for step in sorted(current)]
    def metrics(snapshot):
        return {f'{split}_{name}': value for split in ('train', 'test')
                for name, value in snapshot[split].items()}
    def history(rows):
        return {'step': [row['step'] for row in rows],
                'values': {split: {name: [row[split][name] for row in rows]
                                   for name in rows[-1][split]} for split in ('train', 'test')}}
    def difference(a, b, label):
        return compare_state(a, b, atol=1e-6, rtol=1e-5, label=label)
    items = {'metrics': difference(metrics(left[-1]), metrics(right[-1]), 'metrics'),
             'history': difference(history(left), history(right), 'history')}
    for name, a, b in (('latest', left[-1], right[-1]), ('best', raw['old_best'], raw['new_best'])):
        for part in ('model', 'optimizer', 'scheduler'):
            x, y = a.get(part), b.get(part)
            if x is None and y is None:
                continue
            key = f'checkpoint.{name}.{part}'
            if x is None or y is None:
                items[key] = {'passed': False, 'max_abs_diff': None,
                              'missing': 'left' if x is None else 'right'}
            else:
                if part == 'model':
                    x, y = _model_state(x), _model_state(y)
                items[key] = difference(x, y, key)
    return {'scope': 'observed states', 'atol': 1e-6, 'rtol': 1e-5,
            'items': items, 'passed': all(item['passed'] for item in items.values())}


def run(ctx):
    """Keep the probe's numeric gates without fabricating formal Run records."""
    import torch
    from rpipe.structure.artifact.comparison import write_compare
    from ...structure.algorithm.paired import save_json

    workspace = ctx.state['algorithm'].workspace
    report = ctx.state['probe']
    row = report['runs'][0]
    cell = workspace / 'matrix' / f"{row['data']}_{row['model']}"
    observation_path = Path(row['observations'])
    raw = torch.load(observation_path, map_location='cpu', weights_only=True)
    comparison = compare_observed(raw)
    comparison['source'] = {'path': str(observation_path),
                            'sha256': hashlib.sha256(observation_path.read_bytes()).hexdigest()}
    write_compare(cell / 'RUN_COMPARISON.json', comparison)
    row['run_comparison'] = comparison
    row['passed'] &= comparison['passed']
    report['selected_passed'] &= comparison['passed']
    ctx.state['collected']['metrics']['probe_passed'] = report['selected_passed']
    ctx.state['result_draft']['metrics']['probe_passed'] = report['selected_passed']
    save_json(cell / 'COMPARISON.json', row)
    save_json(workspace / 'COMPARISON.json', report)
    if not comparison['passed']:
        raise RuntimeError(f'observed state comparison failed; evidence: {cell / "RUN_COMPARISON.json"}')

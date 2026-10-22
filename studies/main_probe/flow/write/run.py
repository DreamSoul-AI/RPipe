"""Project observed probe snapshots into inspectable Run artifacts.

No model execution or checkpoint selection occurs here. The probe keeps its
stricter RNG, input, count, and per-segment numerical gates.
"""

from pathlib import Path


def write_observed_run(root, *, snapshots, best, data, model, implementation):
    import json
    import torch
    from types import SimpleNamespace
    from rpipe.structure.artifacts import write_config, write_json as write_result
    from rpipe.structure.artifacts import atomic_write_text

    root = Path(root)
    if root.parent.name != 'runs':
        raise ValueError('observed Run must be under a runs directory')
    if root.exists():
        raise FileExistsError(f'preserve existing probe Run: {root}')
    layout = SimpleNamespace(config_path=root / 'config.yaml', assets_dir=root / 'assets', result_path=root / 'result.json')
    layout.assets_dir.mkdir(parents=True)
    config = {'id': root.name, 'seed': 0, 'data': {'name': data}, 'model': {'name': model},
              'algorithm': {'mode': 'train', 'num_steps': 60},
              'description': f'observed {implementation} probe; normalized state key prefixes'}
    write_config(layout.config_path, config)
    ordered = [snapshots[step] for step in sorted(snapshots)]
    final = ordered[-1]
    metrics = {f'{split}_{key}': value for split in ('train', 'test') for key, value in final[split].items()}
    history = {'splits': {split: {key: {'history': [row[split][key] for row in ordered]}
                                 for key in final[split]} for split in ('train', 'test')}}
    atomic_write_text(layout.assets_dir / 'tracker' / 'tracker_state.json', json.dumps(history, indent=2))
    folder = layout.assets_dir / 'checkpoints'
    folder.mkdir(parents=True, exist_ok=True)
    for name, snapshot in [('latest', final), ('best', best)]:
        # Normalize only the known wrapper prefixes; preserve every buffer.
        state = {(key[6:] if key.startswith('model.') else key[4:] if key.startswith('net.') else key): value
                 for key, value in snapshot['model'].items()}
        if len(state) != len(snapshot['model']):
            raise ValueError('model wrapper prefix normalization would discard a state key')
        torch.save({**snapshot, 'model': state}, folder / f'{name}.pt')
    write_result(layout.result_path, {'status': 'succeeded', 'control': config, 'metrics': metrics,
                                     'paths': {'artifact': str(root), 'assets': str(layout.assets_dir)},
                                     'projection': {'implementation': implementation,
                                                    'model_prefixes': ['model.', 'net.'],
                                                    'source': 'observed snapshots; no computation replay'}})
    return root


def run(ctx):
    """Write observed artifacts and validate them before the outer result commits."""
    import torch
    from rpipe.api import compare_runs, write_compare
    from ...structure.algorithm.paired import save_json

    workspace = ctx.state['algorithm'].workspace
    report = ctx.state['probe']
    row = report['runs'][0]
    cell = workspace / 'matrix' / f"{row['data']}_{row['model']}"
    raw = torch.load(row['observations'], map_location='cpu', weights_only=False)
    observed = cell / 'observed' / 'runs'
    original = write_observed_run(observed / 'original', snapshots=raw['old_snaps'], best=raw['old_best'],
                                 data=row['data'], model=row['model'], implementation='original main')
    current = write_observed_run(observed / 'current', snapshots=raw['current_snaps'], best=raw['new_best'],
                                data=row['data'], model=row['model'], implementation='current RPipe')
    comparison = compare_runs(original, current, atol=1e-6, rtol=1e-5, checkpoints=['latest', 'best'])
    write_compare(cell / 'RUN_COMPARISON.json', comparison)
    row['run_comparison'] = comparison
    row['passed'] &= comparison['passed']
    report['selected_passed'] &= comparison['passed']
    ctx.state['collected']['metrics']['probe_passed'] = report['selected_passed']
    ctx.state['result_draft']['metrics']['probe_passed'] = report['selected_passed']
    save_json(cell / 'COMPARISON.json', row)
    save_json(workspace / 'COMPARISON.json', report)
    if not comparison['passed']:
        raise RuntimeError(f'library Run comparison failed; evidence: {cell / "RUN_COMPARISON.json"}')

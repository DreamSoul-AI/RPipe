"""Manual native Flow capacity validation; all computation remains in RPipe."""
from __future__ import annotations


import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import time
import uuid

STUDY = Path(__file__).resolve().parents[2]


def worker_register(ctx):
    from runpy import run_path
    recipe = run_path(str(STUDY / 'recipe.py'))
    recipe['install'](Path(INPUT_DATA_ROOT), ctx.seed, archive_root=ctx.assets_dir / 'source-4ccb28d')


def worker_before(ctx):
    import torch
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    torch.cuda.reset_peak_memory_stats()
    ctx.state['capacity_started'] = time.perf_counter()


def worker_prepare(ctx):
    runtime = ctx.state['runtime']
    if not runtime['deterministic'] or any(runtime[k] for k in ('cudnn_benchmark', 'cudnn_allow_tf32', 'matmul_allow_tf32')):
        raise RuntimeError('capacity validation requires the declared FP32 runtime')


def worker_write(ctx):
    import torch
    from rpipe.structure.artifacts import atomic_write_text
    body = {'peak_allocated_bytes': torch.cuda.max_memory_allocated(),
            'peak_reserved_bytes': torch.cuda.max_memory_reserved(),
            'flow_seconds': time.perf_counter() - ctx.state['capacity_started'],
            'runtime': ctx.state['runtime'], 'execute': ctx.state['execute']}
    atomic_write_text(ctx.layout.assets_dir / 'capacity.json', json.dumps(body, indent=2))


def _gpu_sample():
    raw = subprocess.check_output(['nvidia-smi', '--query-gpu=index,memory.used,memory.free,utilization.gpu',
        '--format=csv,noheader,nounits'], text=True, timeout=10)
    rows = []
    for line in raw.splitlines():
        index, used, free, utilization = [int(v.strip()) for v in line.split(',')]
        rows.append({'gpu': index, 'used_bytes': used * 1024**2, 'free_bytes': free * 1024**2,
                     'utilization_pct': utilization, 'time': time.time()})
    return rows


def main(recipe, argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--models', nargs='+', choices=['cnn', 'resnet18', 'mixed'], default=['cnn', 'resnet18', 'mixed'])
    parser.add_argument('--rounds', nargs='+', type=int, choices=[1, 2, 3], default=[1, 2, 3])
    args = parser.parse_args(argv)
    args.run_root = recipe.external_root(args.run_root)
    if args.rounds[0] != 1 or len(set(args.rounds)) != len(args.rounds) or len(set(args.models)) != len(args.models):
        parser.error('use unique models and rounds, starting with round1 as the strict baseline')
    recipe.bootstrap()
    from rpipe.structure.artifacts import atomic_write_text
    from rpipe.structure.artifacts import load_config, write_config
    from rpipe.api import plan, make, run as run_spec, read_run, compare_runs
    ident = 'capacity-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    root = args.run_root / 'validation' / ident
    root.mkdir(parents=True, exist_ok=False)
    original = recipe.source_manifest()
    import rpipe
    report = {'measurement_id': ident, 'source_hashes': original, 'groups': [],
              'runtime_package': str(Path(rpipe.__file__).resolve()), 'expected_groups': len(args.models) * len(args.rounds),
              'scope': 'native six-phase Flow, three seeds, 800 steps, full test; no long matrix',
              'output_local_only': str(root)}
    target = root / 'REPORT.json'
    baseline = {}
    proxy = ('import importlib.util\n'
             f"s = importlib.util.spec_from_file_location('_capacity_worker', {str(Path(__file__).resolve())!r})\n"
             'w = importlib.util.module_from_spec(s)\ns.loader.exec_module(w)\n'
             + f'w.INPUT_DATA_ROOT = {str(args.run_root / "shared" / "data")!r}\n')
    for model in args.models:
        for round_size in args.rounds:
            folder = root / 'declarations' / f'{model}-round{round_size}'
            folder.mkdir(parents=True)
            combinations = [('cnn', 0), ('resnet18', 0), ('linear', 0)] if model == 'mixed' else [(model, n) for n in range(3)]
            write_config(folder / 'study.yaml', {'study': ident, 'recipe': 'recipe.py', 'freeze': True,
                'flow': {'study_phases': True, 'prepare_shared': False},
                'axes': {'model.name': [m for m, _ in combinations]} if model == 'mixed' else {},
                'seeds': [0] if model == 'mixed' else [0, 1, 2]})
            write_config(folder / 'experiment_config.yaml', {'version': f'{ident}-{model}-{round_size}',
                'data': {'name': 'CIFAR10', 'source': recipe.SOURCE, 'config': {'batch_size': 250, 'pin_memory': True, 'num_workers': 0}},
                'model': {'name': model if model != 'mixed' else 'cnn', 'source': recipe.SOURCE},
                'algorithm': recipe.algorithm_config(800), 'original_source_digest': original['source_digest'],
                'system': {'device': 'cuda', 'deterministic': True, 'cudnn_deterministic': True,
                           'cudnn_benchmark': False, 'cudnn_allow_tf32': False, 'matmul_allow_tf32': False}})
            atomic_write_text(folder / 'recipe.py', proxy + 'register = w.worker_register\n')
            for phase, exports in [('prepare', 'before = w.worker_before\nrun = w.worker_prepare\n'),
                                   ('write', 'run = w.worker_write\n')]:
                path = folder / 'flow' / phase
                path.mkdir(parents=True)
                atomic_write_text(path / 'run.py', proxy + exports)
                atomic_write_text(path / '__init__.py', 'from .run import before, run\n' if phase == 'prepare' else 'from .run import run\n')
            # Mixed factors use seed0 consistently; the same cells repeat in every round.
            specs = plan(folder, run_root=root / 'output')
            make(specs)
            samples = _gpu_sample()
            if samples[0]['free_bytes'] < round_size * 4 * 1024**3 * 1.25:
                raise RuntimeError('not enough free GPU memory for the bounded capacity measurement')
            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=round_size) as pool:
                futures = [pool.submit(run_spec, spec) for spec in specs]
                while not all(future.done() for future in futures):
                    samples.extend(_gpu_sample())
                    time.sleep(2)
                outcomes = [future.result() for future in futures]
            codes = [0 if outcome.status == 'succeeded' else 1 for outcome in outcomes]
            group = {'model': model, 'round': round_size, 'wall_seconds': time.perf_counter() - start,
                     'exit_codes': codes, 'gpu_samples': samples, 'runs': [], 'comparisons': []}
            passed = len(codes) == 3 and all(code == 0 for code in codes)
            for spec in specs:
                run = spec.run_dir
                cfg = spec.to_config()
                result = read_run(spec)
                if result.get('status') != 'succeeded' or not (run / 'assets/capacity.json').is_file():
                    group['runs'].append({'run_id': spec.id, 'status': result.get('status'), 'error': result.get('error')})
                    passed = False
                    continue
                capacity = json.loads((run / 'assets/capacity.json').read_text(encoding='utf-8'))
                tracker = json.loads((run / 'assets/tracker/tracker_state.json').read_text(encoding='utf-8'))
                history = tracker.get('splits', {}).get('test', {}).get('Accuracy', {}).get('history', [])
                passed &= capacity['execute']['steps'] == 800 and len(history) == 4
                key = (model, cfg['model']['name'], cfg['seed'])
                if round_size == 1:
                    baseline[key] = run
                else:
                    comparison = compare_runs(baseline[key], run, checkpoints=['latest', 'best'])
                    # Elapsed time differs by design; checkpoint/history remain strict zero-tolerance gates.
                    passed &= all(item['passed'] for name, item in comparison['items'].items() if name != 'metrics')
                    group['comparisons'].append(comparison)
                group['runs'].append({'run_id': spec.id, 'seed': cfg['seed'], 'model': cfg['model']['name'],
                                      'capacity': capacity, 'metrics': result['metrics'], 'test_accuracy': history})
            group['passed'] = bool(passed)
            report['groups'].append(group)
            report['source_unchanged'] = original == recipe.source_manifest()
            report['complete'] = len(report['groups']) == report['expected_groups']
            quality = report['source_unchanged'] and all(g['passed'] for g in report['groups'])
            report['passed'] = report['complete'] and quality
            report['status'] = 'failed' if not quality else 'passed' if report['complete'] else 'running'
            atomic_write_text(target, json.dumps(report, indent=2, ensure_ascii=False))
            print(json.dumps({'report': str(target), 'model': model, 'round': round_size, 'passed': group['passed']}, ensure_ascii=False), flush=True)
            if not quality:
                return 1
    return 0

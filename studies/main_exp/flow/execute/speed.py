"""Manual historical/current stage calibration, invoked by recipe.py, not a hook."""
from __future__ import annotations


import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid


def correct_samples(accuracy, count):
    """Recover classification counts, not a tolerance on correct predictions.

    >>> correct_samples(97.74000000000001, 10000)
    9774
    >>> correct_samples(89.15, 10000) - correct_samples(89.14, 10000)
    1
    >>> correct_samples(89.145, 10000)
    Traceback (most recent call last):
        ...
    ValueError: full-test Accuracy does not represent an integer sample count
    """
    raw = float(accuracy) * count / 100
    if not math.isfinite(raw) or not 0 <= raw <= count:
        raise ValueError('invalid full-test Accuracy')
    correct = round(raw)
    # Batch Accuracy originates in float32; validate its rounding envelope before decoding.
    rounding = count * 2**-23
    if rounding >= .5 or abs(raw - correct) > rounding:
        raise ValueError('full-test Accuracy does not represent an integer sample count')
    return correct


def main(recipe, argv):
    from rpipe.structure.artifact.io import atomic_write_text

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--data', choices=['MNIST', 'CIFAR10'], default='CIFAR10')
    parser.add_argument('--model', choices=['cnn', 'resnet18'], required=True)
    parser.add_argument('--steps', type=int, default=800)
    parser.add_argument('--order', choices=['old-first', 'current-first'], default='old-first')
    args = parser.parse_args(argv)
    args.run_root = recipe.external_root(args.run_root)
    expected = json.loads((recipe.STUDY / 'docs/EXPECTED_DATA.json').read_text(encoding='utf-8'))
    raw_hashes = {}
    for name, sha in expected.items():
        name = name.replace('\\', '/')
        if name.startswith('data/' + args.data + '/'):
            path = args.run_root / 'shared' / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                parser.error(f'missing or changed raw data: {path}')
            raw_hashes[name] = sha
    if not raw_hashes:
        parser.error(f'no expected raw hashes for {args.data}')
    mapping = recipe.algorithm_config(args.steps)
    if args.steps < 800:
        parser.error('at least 800 steps: warmup plus three measured segments')
    recipe.bootstrap()
    os.environ['CUBLAS_WORKSPACE_CONFIG'] = ':4096:8'
    import torch
    from rpipe.structure.control import AlgorithmConfig
    from rpipe.structure.algorithm.train import TrainAlgorithm
    from rpipe.structure.algorithm.tracker import AlgorithmTracker
    from rpipe.structure.control import DataConfig
    from rpipe.structure.data.factory import build_data
    from rpipe.structure.control import ModelConfig
    from rpipe.structure.model.factory import build_model
    from rpipe.structure.control import SystemConfig
    from rpipe.structure.system.factory import build_system
    from rpipe.structure.system.runtime import apply_runtime

    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    controls = SystemConfig.from_mapping({'device': 'cuda', 'deterministic': True,
        'cudnn_deterministic': True, 'cudnn_benchmark': False,
        'cudnn_allow_tf32': False, 'matmul_allow_tf32': False})
    ident = 'speed-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    output = args.run_root / 'validation' / ident
    output.mkdir(parents=True, exist_ok=False)
    archive = recipe.install(args.run_root / 'shared' / 'data', 0, archive_root=output / 'source-4ccb28d')
    report = {'measurement_id': ident, 'historical_commit': recipe.COMMIT,
        'current_commit': subprocess.check_output(['git', '-c', 'safe.directory=' + str(recipe.REPO), 'rev-parse', 'HEAD'], cwd=recipe.REPO, text=True).strip(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'recipe_sha256': hashlib.sha256(Path(recipe.__file__).read_bytes()).hexdigest(),
        'data_manifest_sha256': hashlib.sha256((args.run_root / 'shared' / 'DATA_MANIFEST.json').read_bytes()).hexdigest(),
        'torch': torch.__version__, 'device': torch.cuda.get_device_name(),
        'data': args.data, 'raw_sha256': raw_hashes, 'model': args.model, 'seed': 0, 'steps': args.steps,
        'sampler_scheduler_steps': 80000, 'batch': 250, 'test_size': 10000,
        'eval_period': 200, 'cpu_threads': 2, 'order': args.order,
        'controls': {'deterministic': True, 'benchmark': False, 'cudnn_tf32': False, 'matmul_tf32': False},
        'scope': 'serial real train/test/checkpoint; first segment warmup; no probe input hashing or snapshots',
        'memory_scope': 'cumulative PyTorch peaks from setup through each phase; excludes context/driver/other processes',
        'output_dir_local_only': str(output), 'sides': {}}
    report['source_hashes'] = recipe.source_manifest()

    def sync():
        torch.cuda.synchronize()

    def timed(call):
        sync()
        start = time.perf_counter()
        result = call()
        sync()
        return result, time.perf_counter() - start

    def memory():
        return {'peak_allocated_bytes': torch.cuda.max_memory_allocated(),
                'peak_reserved_bytes': torch.cuda.max_memory_reserved()}

    def runtime():
        return {'deterministic': torch.are_deterministic_algorithms_enabled(),
                'benchmark': torch.backends.cudnn.benchmark, 'cudnn_deterministic': torch.backends.cudnn.deterministic,
                'cudnn_tf32': torch.backends.cudnn.allow_tf32, 'matmul_tf32': torch.backends.cuda.matmul.allow_tf32}

    def old():
        archive.configure(args.data, args.model, 0, 'cuda')
        apply_runtime(0, controls)
        torch.cuda.reset_peak_memory_stats()
        start = time.perf_counter()
        datasets = archive.datasets(args.run_root / 'shared' / 'data', args.data)
        module = archive.model.make_model(args.model).to('cuda')
        initial = recipe._state_cpu(module)
        optimizer = archive.model.make_optimizer(module.parameters(), args.model)
        scheduler = archive.model.make_scheduler(optimizer, args.model)
        archive.dataset.process_dataset(datasets)
        loaders = archive.dataset.make_data_loader(datasets, archive.config.cfg[args.model]['batch_size'])
        iterator = enumerate(loaders['train'])
        logger = archive.metric.make_logger(str(output / 'old/logger'))
        sync()
        setup = time.perf_counter() - start
        setup_memory = memory()
        start = time.perf_counter()
        segments = []
        while archive.config.cfg['iteration'] < args.steps:
            _, train = timed(lambda: archive.train_model.train(iterator, module, optimizer, scheduler, logger))
            train_memory = memory()
            _, evaluation = timed(lambda: archive.train_model.test(loaders['test'], module, logger))
            eval_memory = memory()
            metrics = {k: float(v) for k, v in logger.mean.items() if k.startswith('test/')}
            def save():
                payload = {'cfg': archive.config.cfg, 'model_state_dict': module.state_dict(),
                    'optimizer_state_dict': optimizer.state_dict(), 'scheduler_state_dict': scheduler.state_dict(),
                    'logger_state_dict': logger.state_dict()}
                archive.module.save(payload, str(output / 'old/checkpoint/model'))
                if logger.compare('test'):
                    (output / 'old/best').mkdir(exist_ok=True)
                    shutil.copy(output / 'old/checkpoint/model', output / 'old/best/model')
                logger.reset()
            _, checkpoint = timed(save)
            segments.append({'step': int(archive.config.cfg['iteration']), 'train_seconds': train,
                'eval_seconds': evaluation, 'checkpoint_seconds': checkpoint, 'test': metrics,
                'memory': {'train': train_memory, 'eval': eval_memory, 'checkpoint': memory()}})
        sync()
        side = {'setup_seconds': setup, 'run_seconds': time.perf_counter() - start,
            **memory(), 'setup_memory': setup_memory, 'runtime': runtime(), 'segments': segments}
        final = recipe._state_cpu(module)
        logger.writer.close()
        return initial, final, side

    def current():
        archive.configure(args.data, args.model, 0, 'cuda')
        apply_runtime(0, controls)
        torch.cuda.reset_peak_memory_stats()
        start = time.perf_counter()
        assets = output / 'current/assets'
        system = build_system(controls, assets)
        data = build_data(DataConfig.from_mapping({'name': args.data, 'source': recipe.SOURCE,
            'config': {'batch_size': 250, 'pin_memory': True, 'num_workers': 0}}), assets, seed=0)
        model = build_model(ModelConfig.from_mapping({'name': args.model, 'source': recipe.SOURCE}),
            assets, data_meta=data.meta, system=system)
        initial = recipe._state_cpu(model.module)
        tracker = AlgorithmTracker(assets)
        segments = []
        class MeasuredTrain(TrainAlgorithm):
            def on_eval_period(self, *pos, **kw):
                sync()
                before = time.perf_counter()
                train = before - self.segment_start
                train_memory = memory()
                result = super().on_eval_period(*pos, **kw)
                sync()
                extra = pos[5] if len(pos) > 5 else kw.get('extra', {})
                segments.append({'step': int(extra['step']), 'train_and_bookkeeping_seconds': train,
                    'eval_seconds': time.perf_counter() - before, 'checkpoint_seconds': 0.,
                    'memory': {'train': train_memory, 'eval': memory()}})
                return result

            def on_checkpoint(self, *pos, **kw):
                _, elapsed = timed(lambda: super(MeasuredTrain, self).on_checkpoint(*pos, **kw))
                if segments:
                    segments[-1]['checkpoint_seconds'] += elapsed
                    segments[-1]['memory']['checkpoint'] = memory()
                self.segment_start = time.perf_counter()
        algorithm = MeasuredTrain(AlgorithmConfig.from_mapping(mapping))
        sync()
        setup = time.perf_counter() - start
        setup_memory = memory()
        algorithm.segment_start = time.perf_counter()
        summary, elapsed = timed(lambda: algorithm.compute(data, model, system, tracker, cancel_check=lambda: None))
        histories = {k: list(v.history) for k, v in tracker._meters['test'].items()}
        for i, row in enumerate(segments):
            row['test'] = {'test/' + k: float(v[i]) for k, v in histories.items()}
        side = {'setup_seconds': setup, 'run_seconds': elapsed,
            **memory(), 'setup_memory': setup_memory, 'runtime': runtime(), 'segments': segments, 'summary': summary}
        return initial, recipe._state_cpu(model.module), side

    states = {}
    for label in (['old', 'current'] if args.order == 'old-first' else ['current', 'old']):
        initial, final, side = (old if label == 'old' else current)()
        states[label] = (initial, final)
        report['sides'][label] = side
        torch.cuda.empty_cache()
    report['initial_state'] = recipe._compare_states(states['old'][0], states['current'][0])
    report['final_state'] = recipe._compare_states(states['old'][1], states['current'][1])
    diffs = []
    assert len(report['sides']['old']['segments']) == len(report['sides']['current']['segments']) == args.steps // 200
    for left, right in zip(report['sides']['old']['segments'], report['sides']['current']['segments']):
        assert left['step'] == right['step']
        diffs.append({'step': left['step'], 'loss_absolute_difference': abs(left['test']['test/Loss'] - right['test']['test/Loss']),
            'accuracy_absolute_difference': abs(left['test']['test/Accuracy'] - right['test']['test/Accuracy']),
            'old_correct_samples': correct_samples(left['test']['test/Accuracy'], report['test_size']),
            'current_correct_samples': correct_samples(right['test']['test/Accuracy'], report['test_size'])})
    report['numeric_segments'] = diffs
    report['source_unchanged'] = report['source_hashes'] == recipe.source_manifest()
    report['passed'] = report['source_unchanged'] and report['initial_state']['exact'] and report['final_state']['within_gate'] and all(
        d['loss_absolute_difference'] <= 1e-6 and d['old_correct_samples'] == d['current_correct_samples'] for d in diffs)
    target = output / 'REPORT.json'
    target.parent.mkdir(exist_ok=True)
    atomic_write_text(target, json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'report': str(target), 'passed': report['passed']}, ensure_ascii=False), flush=True)
    return 0 if report['passed'] else 1

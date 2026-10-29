"""Register historical builders; explicit preflight/calibrate commands never run on import."""
from __future__ import annotations


import copy
from contextlib import contextmanager, ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Iterable
import uuid


def _load_structure():
    name = '_main_exp_structure_' + uuid.uuid4().hex
    path = Path(__file__).resolve().parent / 'structure' / '__init__.py'
    spec = importlib.util.spec_from_file_location(name, path, submodule_search_locations=[str(path.parent)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_structure = _load_structure()
STUDY, REPO = _structure.STUDY, _structure.REPO
COMMIT, SOURCE, HORIZON = _structure.COMMIT, _structure.SOURCE, _structure.HORIZON
DATA_NAMES, MODEL_NAMES = _structure.DATA_NAMES, _structure.MODEL_NAMES
bootstrap, install, Observation = _structure.bootstrap, _structure.install, _structure.Observation
_observed_dicts = _structure._observed_dicts


def source_manifest() -> dict[str, str]:
    from rpipe.structure.artifact.provenance import source_digest
    return {'source_digest': source_digest(STUDY)}


def capture_environment() -> dict[str, str]:
    from importlib.metadata import version
    return {'python': sys.version, **{name: version(name) for name in ('torch', 'numpy')}}


def external_root(value: Path | str) -> Path:
    root = Path(value).resolve()
    if root.is_relative_to(REPO) or REPO.is_relative_to(root):
        raise ValueError('run root must be separate from the source checkout')
    return root


def register(ctx: Any) -> None:
    """RPipe prepare hook (``study.yaml`` ``recipe``): runs in every Run's process."""
    os.environ['CUBLAS_WORKSPACE_CONFIG'] = ':4096:8'
    import torch

    torch.set_num_threads(2)
    if ctx.config['algorithm']['mode'] == 'train':
        checkpoints = Path(ctx.assets_dir) / 'checkpoints'
        if checkpoints.is_dir() and any(checkpoints.iterdir()):
            raise RuntimeError(f'{ctx.run_id}: interrupted training has checkpoints; preserve it and use a new '
                               'version for a fresh continuous run')
    report = json.loads((ctx.study_root / 'summary' / 'PREFLIGHT.json').read_text(encoding='utf-8'))
    expected = {(d, m) for d in DATA_NAMES for m in MODEL_NAMES}
    if report.get('passed') is not True or {(r['data'], r['model']) for r in report.get('runs', [])} != expected:
        raise RuntimeError('the same-device original/native preflight must pass before launch')
    if report.get('device') != str(ctx.config['system'].get('device', 'cpu')):
        raise RuntimeError('Run device differs from the same-device preflight')
    if (report.get('environment') or {}).get('torch') != torch.__version__:
        raise RuntimeError('Torch environment changed after preflight')
    if report.get('source_hashes') != source_manifest():
        raise RuntimeError('main_exp preflight sources changed; rerun preflight before launch')
    for flag in ('cudnn_allow_tf32', 'matmul_allow_tf32'):
        if ctx.config['system'].get(flag) is not False or report.get('controls', {}).get(flag) is not False:
            raise RuntimeError(f'main_exp requires {flag}=false in both Run and preflight')
    install(ctx.shared_data_dir, seed=ctx.seed, archive_root=ctx.assets_dir / 'source-4ccb28d')


def _rng_state() -> dict[str, Any]:
    import torch
    return {'cpu': torch.get_rng_state().clone(),
            'cuda': [value.clone() for value in torch.cuda.get_rng_state_all()] if torch.cuda.is_available() else []}


def _rng_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    import torch
    return torch.equal(left['cpu'], right['cpu']) and len(left['cuda']) == len(right['cuda']) and all(
        torch.equal(a, b) for a, b in zip(left['cuda'], right['cuda'])
    )


def _state_cpu(module: Any) -> dict[str, Any]:
    return {name: value.detach().cpu().clone() for name, value in module.state_dict().items()}


def _compare_states(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    import torch
    keys = set(left) == set(right)
    max_diff = 0.0
    integers_equal = True
    close = keys
    exact = keys
    for name in set(left) & set(right):
        a, b = left[name], right[name]
        if a.shape != b.shape or a.dtype != b.dtype:
            close = exact = False
            continue
        equal = torch.equal(a, b)
        exact &= equal
        if a.is_floating_point():
            max_diff = max(max_diff, float((a - b).abs().max()))
            close &= bool(torch.allclose(a, b, atol=1e-6, rtol=1e-5))
        else:
            integers_equal &= equal
            close &= equal
    return {'keys_equal': keys, 'exact': bool(exact), 'integers_equal': bool(integers_equal),
            'max_absolute_difference': max_diff, 'within_gate': bool(close)}


def algorithm_config(steps: int = HORIZON) -> dict[str, Any]:
    if not 0 < steps <= HORIZON or steps % 200:
        raise ValueError('historical budget must be a positive multiple of 200, at most 80000')
    return {'source': 'custom_torch', 'mode': 'train', 'num_steps': steps, 'progress_unit': 'step',
            'eval_period': 200, 'eval_num_steps': -1, 'log_period': 200,
            'checkpoint': 'latest', 'checkpoint_period': 200, 'save_best': True,
            'best_metric': 'Accuracy', 'best_mode': 'max', 'best_split': 'test',
            'optimizer': 'SGD', 'lr': .01, 'momentum': .9, 'nesterov': True,
            'weight_decay': .0005, 'max_grad_norm': 1., 'scheduler': 'cosine',
            'T_max': HORIZON, 'eta_min': 0., 'resume': False}


@contextmanager
def _owned(resource):
    """Close this probe's resource while retaining its first failure."""
    primary = None
    try:
        yield resource
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            resource.close()
            if getattr(resource, 'diagnostics_complete', True) is False:
                raise RuntimeError('preflight resource diagnostics are incomplete')
        except BaseException as error:
            if primary is None:
                raise
            primary.cleanup_errors = (*getattr(primary, 'cleanup_errors', ()), error)


def preflight(data_root: Path | str, output_dir: Path | str, *, steps: int = 200, seed: int = 0,
              device: str = 'cuda', combinations: Iterable[tuple[str, str]] | None = None) -> dict[str, Any]:
    """Explicit real-data probe using untouched original train/test functions.

    Uniform deterministic controls allow a cross-implementation numerical gate.
    A new output directory is required so prior evidence cannot be overwritten.
    This probe verifies a prefix, never claims historical long-curve reproduction.
    """
    from rpipe.structure.artifact.io import atomic_write_text

    bootstrap()
    os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
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

    mapping = algorithm_config(steps)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    archive = install(data_root, seed, archive_root=output / 'source-4ccb28d')
    system_config = SystemConfig.from_mapping({'device': device, 'deterministic': True,
                                              'cudnn_benchmark': False, 'cudnn_deterministic': True,
                                              'cudnn_allow_tf32': False, 'matmul_allow_tf32': False})
    pairs = list(combinations or [(d, m) for d in DATA_NAMES for m in MODEL_NAMES])
    result: dict[str, Any] = {'historical_commit': COMMIT, 'current_commit': subprocess.check_output(
        ['git', '-c', 'safe.directory=' + str(REPO), 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
        'steps': steps, 'sampler_steps': HORIZON, 'seed': seed, 'device': device,
        'torch': torch.__version__, 'controls': {'deterministic': True, 'benchmark': False,
        'CUBLAS_WORKSPACE_CONFIG': os.environ['CUBLAS_WORKSPACE_CONFIG'],
        'cudnn_allow_tf32': False, 'matmul_allow_tf32': False},
        'recipe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'runs': []}
    start = time.perf_counter()

    class CapturedTrain(TrainAlgorithm):
        def __init__(self, config: Any, *, checkpoint_dir: Path) -> None:
            super().__init__(config, checkpoint_dir=checkpoint_dir)
            self.snapshots: dict[int, dict[str, Any]] = {}

        def make_optimizer(self, module: Any):
            self.optimizer = super().make_optimizer(module)
            return self.optimizer

        def make_scheduler(self, optimizer: Any, t_max: int):
            self.scheduler = super().make_scheduler(optimizer, t_max)
            return self.scheduler

        def on_checkpoint(self, tracker: Any, logger: Any, data: Any, model: Any,
                          system: Any, extra: dict[str, Any] | None = None) -> None:
            super().on_checkpoint(tracker, logger, data, model, system, extra)
            extra = extra or {}
            step = int(extra.get('step', 0))
            if step > 0 and step % 200 == 0:
                self.snapshots[step] = {'model': _state_cpu(model.module), 'rng': _rng_state(),
                                        'scheduler': copy.deepcopy(self.scheduler.state_dict())}

    for data_name, model_name in pairs:
        cell = output / f'{data_name}_{model_name}'
        cell.mkdir()
        archive.configure(data_name, model_name, seed, device)
        apply_runtime(seed, system_config)
        datasets = archive.datasets(Path(data_root).resolve(), data_name)
        original = archive.model.make_model(model_name).to(device)
        original_init = _state_cpu(original)
        original_init_rng = _rng_state()
        optimizer = archive.model.make_optimizer(original.parameters(), model_name)
        scheduler = archive.model.make_scheduler(optimizer, model_name)
        archive.dataset.process_dataset(datasets)
        loaders = archive.dataset.make_data_loader(datasets, archive.config.cfg[model_name]['batch_size'])
        original_obs = {'train': Observation(), 'test': Observation()}
        train_iterator = enumerate(_observed_dicts(loaders['train'], original_obs['train']))
        logger = archive.metric.make_logger(str(cell / 'original_tensorboard'))
        with _owned(logger.writer):
            original_segments = []
            original_snapshots: dict[int, dict[str, Any]] = {}
            while archive.config.cfg['iteration'] < steps:
                archive.train_model.train(train_iterator, original, optimizer, scheduler, logger)
                archive.train_model.test(_observed_dicts(loaders['test'], original_obs['test']), original, logger)
                original_segments.append({'step': archive.config.cfg['iteration'],
                                          'train': {k.split('/')[1]: v for k, v in logger.mean.items() if k.startswith('train/')},
                                          'test': {k.split('/')[1]: v for k, v in logger.mean.items() if k.startswith('test/')}})
                step = int(archive.config.cfg['iteration'])
                snapshot = {'model': _state_cpu(original), 'rng': _rng_state(),
                            'scheduler': copy.deepcopy(scheduler.state_dict())}
                original_snapshots[step] = snapshot
                torch.save(snapshot, cell / f'original_step_{step}.pt')
                logger.reset()
            original_final = _state_cpu(original)
            original_rng = _rng_state()
            original_scheduler = scheduler.state_dict()
            torch.save({'model_state_dict': original_final, 'optimizer_state_dict': optimizer.state_dict(),
                        'scheduler_state_dict': original_scheduler, 'logger_state_dict': logger.state_dict(),
                        'step': steps, 'rng': original_rng}, cell / 'original.pt')

        archive.configure(data_name, model_name, seed, device)
        apply_runtime(seed, system_config)
        assets = cell / 'current' / 'assets'
        with ExitStack() as resources:
            system = build_system(system_config, assets)
            resources.enter_context(_owned(system.logger))
            data = build_data(DataConfig.from_mapping({'name': data_name, 'source': SOURCE,
                                     'config': {'batch_size': 250, 'test_batch_ratio': 1,
                                                'pin_memory': True, 'num_workers': 0}}), assets, seed=seed)
            resources.enter_context(_owned(data))
            model = build_model(ModelConfig.from_mapping({'name': model_name, 'source': SOURCE}),
                                       assets, data_meta=data.meta, system=system)
            initial = _compare_states(original_init, _state_cpu(model.module))
            init_rng = _rng_equal(original_init_rng, _rng_state())
            data.observations = {'train': Observation(), 'test': Observation()}
            tracker = AlgorithmTracker(assets)
            algorithm = CapturedTrain(AlgorithmConfig.from_mapping(mapping), checkpoint_dir=(assets / 'checkpoints').resolve())
            summary = algorithm.compute(data, model, system, tracker, cancel_check=lambda: None)
            final = _compare_states(original_final, _state_cpu(model.module))
            final_rng = _rng_equal(original_rng, _rng_state())
            current_scheduler = algorithm.scheduler.state_dict()
            scheduler_equal = original_scheduler == current_scheduler
            # Compare numerical segments rather than rounded terminal messages.
            current_segments = []
            for index, segment in enumerate(original_segments):
                body = {'step': segment['step']}
                for split in ('train', 'test'):
                    body[split] = {name: meter.history[index] for name, meter in tracker._meters[split].items()}
                current_segments.append(body)
            loss_diff = accuracy_diff = 0.
            for old, new in zip(original_segments, current_segments):
                for split in ('train', 'test'):
                    loss_diff = max(loss_diff, abs(old[split]['Loss'] - new[split]['Loss']))
                    accuracy_diff = max(accuracy_diff, abs(old[split]['Accuracy'] - new[split]['Accuracy']))
            input_old = {key: value.result() for key, value in original_obs.items()}
            input_new = {key: value.result() for key, value in data.observations.items()}
            inputs_equal = input_old == input_new
            segment_comparisons = []
            for old, new in zip(original_segments, current_segments):
                step = old['step']
                old_snapshot, new_snapshot = original_snapshots[step], algorithm.snapshots[step]
                parameters = _compare_states(old_snapshot['model'], new_snapshot['model'])
                rng_equal = _rng_equal(old_snapshot['rng'], new_snapshot['rng'])
                segment_scheduler_equal = old_snapshot['scheduler'] == new_snapshot['scheduler']
                counts = {split: {'original': round(old[split]['Accuracy'] * size / 100),
                                 'current': round(new[split]['Accuracy'] * size / 100),
                                 'samples': size}
                          for split, size in (('train', 200 * 250), ('test', 10000))}
                counts_equal = all(value['original'] == value['current'] for value in counts.values())
                segment_loss_diff = max(abs(old[split]['Loss'] - new[split]['Loss']) for split in ('train', 'test'))
                segment_comparisons.append({'step': step, 'parameters': parameters, 'rng_equal': rng_equal,
                                            'scheduler_equal': segment_scheduler_equal, 'correct_counts': counts,
                                            'correct_counts_equal': counts_equal,
                                            'maximum_loss_absolute_difference': segment_loss_diff,
                                            'passed': bool(parameters['within_gate'] and rng_equal and
                                                           segment_scheduler_equal and counts_equal and segment_loss_diff <= 1e-6)})
            correct_counts_equal = all(value['correct_counts']['test']['original'] ==
                                       value['correct_counts']['test']['current'] for value in segment_comparisons)
            train_correct_counts_equal = all(value['correct_counts']['train']['original'] ==
                                             value['correct_counts']['train']['current'] for value in segment_comparisons)
            row = {'data': data_name, 'model': model_name, 'initial_state': initial, 'final_state': final,
                   'initial_rng_equal': init_rng, 'final_rng_equal': final_rng, 'scheduler_equal': scheduler_equal,
                   'original_scheduler': original_scheduler, 'current_scheduler': current_scheduler,
                   'inputs_equal': inputs_equal, 'original_inputs': input_old, 'current_inputs': input_new,
                   'original_segments': original_segments, 'current_segments': current_segments,
                   'maximum_loss_absolute_difference': loss_diff,
                   'maximum_accuracy_difference_percentage_points': accuracy_diff,
                   'test_correct_counts_equal': correct_counts_equal,
                   'train_correct_counts_equal': train_correct_counts_equal,
                   'segment_comparisons': segment_comparisons, 'summary': summary,
                   'passed': bool(initial['exact'] and final['within_gate'] and init_rng and final_rng and
                                  scheduler_equal and inputs_equal and correct_counts_equal and
                                  train_correct_counts_equal and loss_diff <= 1e-6 and
                                  all(value['passed'] for value in segment_comparisons))}
        result['runs'].append(row)
        result['passed'] = all(value['passed'] for value in result['runs'])
        result['elapsed_seconds'] = time.perf_counter() - start
        atomic_write_text(output / 'COMPARISON.json', json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'preflight {data_name}/{model_name}: passed={row["passed"]} max_parameter_diff={final["max_absolute_difference"]}', flush=True)
    return result


def main(argv: list[str] | None = None) -> int:
    """``preflight``: same-device 600-step gate written to the explicit research version summary."""
    import argparse
    import uuid
    from datetime import datetime, timezone

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == 'diagnose-precision':
        from flow.execute import precision
        return precision.main(sys.modules[__name__], arguments[1:])
    if arguments and arguments[0] == 'calibrate-capacity':
        from flow.execute import capacity
        return capacity.main(sys.modules[__name__], arguments[1:])
    if arguments and arguments[0] == 'calibrate':
        from flow.execute import speed
        return speed.main(sys.modules[__name__], arguments[1:])

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('command', choices=['preflight'])
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--version', default='main-exp-rpipe-refactor-20261021')
    args = parser.parse_args(arguments)
    run_root = external_root(args.run_root)
    bootstrap()
    from rpipe.structure.artifact.io import atomic_write_text
    from rpipe.structure.make import plan

    specs = plan(STUDY, run_root=run_root, version=args.version)
    before = source_manifest()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d')
    output = specs[0].study_root / 'validation' / f'preflight-{stamp}-{uuid.uuid4().hex[:8]}'
    report = preflight(data_root=run_root / 'shared' / 'data', output_dir=output, steps=600, device='cuda')
    if before != source_manifest():
        raise RuntimeError('source/configuration changed during preflight; retained output is not a valid launch gate')
    report.update(source_hashes=before, output_dir=str(output), environment=capture_environment())
    atomic_write_text(specs[0].study_root / 'summary' / 'PREFLIGHT.json', json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get('passed') else 1



if __name__ == '__main__':
    sys.exit(main())

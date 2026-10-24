"""Run pinned main 98648f3 and current RPipe side by side, in an isolated workspace.

Called exclusively by the Study recipe and paired Algorithm inside library Flow.
The current production Factory / TrainAlgorithm / EvalAlgorithm are exercised;
none of the historical Study registries or source files are changed.
"""
from __future__ import annotations

import contextlib
import copy
import gc
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
MAIN = '98648f3a5c7db7dccf3ca806410d5b6fdee9484c'
DATA = ('MNIST', 'CIFAR10')
MODELS = ('linear', 'mlp', 'cnn', 'resnet18')
SEED = 0


def bootstrap() -> None:
    os.environ['CUBLAS_WORKSPACE_CONFIG'] = ':4096:8'
    import numpy  # noqa: F401 -- Windows native-runtime import order
    import torch
    torch.set_num_threads(2)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, body: Any) -> None:
    from rpipe.structure.artifacts import atomic_write_text

    atomic_write_text(path, json.dumps(body, ensure_ascii=False, indent=2) + '\n')


@contextlib.contextmanager
def owned(resource: Any, close: Any = None):
    """Release a local resource without replacing its primary failure."""
    primary = None
    try:
        yield resource
    except BaseException as error:
        primary = error
        raise
    finally:
        cleanup = close if close is not None else getattr(resource, 'close', None)
        if callable(cleanup):
            try:
                cleanup()
            except BaseException as error:
                if primary is None:
                    raise
                primary.cleanup_errors = (*getattr(primary, 'cleanup_errors', ()), error)


def source_manifest() -> dict[str, str]:
    from rpipe.api import source_digest

    study = Path(__file__).resolve().parents[2]
    return {'source_digest': source_digest(study)}


@contextlib.contextmanager
def cwd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class MainArchive:
    ROOTS = ('config', 'module', 'dataset', 'model', 'metric', 'train_model', 'test_model')

    def __init__(self, workspace: Path):
        bootstrap()
        self.workspace = workspace
        self.original_root = workspace / 'original'
        self.archive = workspace / 'source-main-98648f3'
        manifest = []
        for entry in subprocess.check_output(['git', '-c', 'safe.directory=' + str(ROOT), 'ls-tree', '--full-tree', '-r', '-z', MAIN], cwd=ROOT).split(b'\0'):
            if not entry:
                continue
            attributes, filename = entry.split(b'\t', 1)
            _, kind, blob = attributes.decode('ascii').split()
            if kind != 'blob':
                continue
            name = filename.decode('utf-8')
            payload = subprocess.check_output(['git', '-c', 'safe.directory=' + str(ROOT), 'cat-file', 'blob', blob], cwd=ROOT)
            target = self.archive / name
            if target.exists():
                if target.read_bytes() != payload:
                    raise ValueError(f'archived source changed: {target}')
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                handle = target.open('xb')
                try:
                    handle.write(payload)
                except BaseException:
                    try:
                        handle.close()
                    except OSError:
                        pass
                    raise
                else:
                    handle.close()
            manifest.append({'path': name, 'git_blob': blob,
                             'sha256': hashlib.sha256(payload).hexdigest()})
        save_json(workspace / 'ARCHIVE_MANIFEST.json', {'main_commit': MAIN, 'files': manifest})
        self.manifest = manifest
        saved = {name: value for name, value in sys.modules.items() if self.is_old(name)}
        for name in saved:
            del sys.modules[name]
        previous_argv, previous_path = sys.argv, list(sys.path)
        try:
            sys.path.insert(0, str(self.archive / 'src'))
            sys.argv = ['train_model.py']
            with cwd(self.archive / 'src'):
                for name in self.ROOTS:
                    if name in ('train_model', 'test_model'):
                        # Each original CLI normally runs in its own process.
                        # Importing train first adds control_name to cfg; the
                        # independent test parser would then define it twice.
                        self.config.cfg.clear()
                        self.config.cfg.update(copy.deepcopy(self.defaults))
                    setattr(self, name, importlib.import_module(name))
                    if name == 'config':
                        self.defaults = copy.deepcopy(self.config.cfg)
            self.modules = {name: value for name, value in sys.modules.items() if self.is_old(name)}
        finally:
            for name in list(sys.modules):
                if self.is_old(name):
                    del sys.modules[name]
            sys.modules.update(saved)
            sys.path[:] = previous_path
            sys.argv = previous_argv

    @classmethod
    def is_old(cls, name: str) -> bool:
        return any(name == root or name.startswith(root + '.') for root in cls.ROOTS)

    @contextlib.contextmanager
    def aliases(self):
        saved = {name: value for name, value in sys.modules.items() if self.is_old(name)}
        for name in saved:
            del sys.modules[name]
        sys.modules.update(self.modules)
        try:
            yield
        finally:
            for name in list(sys.modules):
                if self.is_old(name):
                    del sys.modules[name]
            sys.modules.update(saved)

    def configure(self, data: str, model: str, device: str = 'cpu') -> dict[str, Any]:
        cfg = self.config.cfg
        cfg.clear()
        cfg.update(copy.deepcopy(self.defaults))
        cfg.update(control={'data_name': data, 'model_name': model}, control_name=f'{data}_{model}',
                   tag=f'0_{data}_{model}', seed=0, step=0, device=device)
        with self.aliases(), cwd(self.original_root):
            self.module.process_control()
        return cfg

    def datasets(self, name: str) -> dict[str, Any]:
        with self.aliases(), cwd(self.original_root):
            result = self.dataset.make_dataset(name, verbose=False)
            self.dataset.process_dataset(result)
        return result

    def verify_exports(self) -> dict[str, Any]:
        differing = [row['path'] for row in self.manifest
                     if sha(self.archive / row['path']) != row['sha256']]
        return {'main_commit': MAIN, 'files_checked': len(self.manifest),
                'manifest_sha256': sha(self.workspace / 'ARCHIVE_MANIFEST.json'),
                'changed_files': differing, 'unchanged': not differing}


def runtime_config(device: str) -> Any:
    from rpipe.structure.contracts import SystemConfig
    return SystemConfig.from_mapping({'device': device, 'deterministic': True,
                                     'cudnn_benchmark': False, 'cudnn_deterministic': True,
                                     'cudnn_allow_tf32': False, 'matmul_allow_tf32': False})


def seed_runtime(device: str) -> Any:
    from rpipe.structure.system.runtime import apply_runtime
    config = runtime_config(device)
    apply_runtime(SEED, config)
    return config


def model_state(module: Any) -> dict[str, Any]:
    return {name: value.detach().cpu().clone() for name, value in module.state_dict().items()}


def cpu_tree(value: Any) -> Any:
    import torch
    if isinstance(value, torch.Tensor):
        return value.detach().cpu().clone()
    if isinstance(value, dict):
        return {key: cpu_tree(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return type(value)(cpu_tree(item) for item in value)
    return copy.deepcopy(value)


def optimizer_compare(left: Any, right: Any) -> dict[str, bool]:
    import torch
    if isinstance(left, torch.Tensor) and isinstance(right, torch.Tensor):
        same_shape = left.shape == right.shape and left.dtype == right.dtype
        exact = same_shape and torch.equal(left, right)
        close = same_shape and (torch.allclose(left, right, atol=1e-6, rtol=1e-5)
                                if left.is_floating_point() else exact)
        return {'exact': bool(exact), 'within_gate': bool(close)}
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            return {'exact': False, 'within_gate': False}
        children = [optimizer_compare(left[key], right[key]) for key in left]
    elif isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            return {'exact': False, 'within_gate': False}
        children = [optimizer_compare(a, b) for a, b in zip(left, right)]
    else:
        equal = type(left) is type(right) and left == right
        return {'exact': bool(equal), 'within_gate': bool(equal)}
    return {key: all(child[key] for child in children) for key in ('exact', 'within_gate')}


def release_memory(device: str) -> None:
    import torch
    gc.collect()
    if device.startswith('cuda'):
        torch.cuda.empty_cache()


def core_state(body: dict[str, Any]) -> dict[str, Any]:
    # Main's Base uses model.*, current InputNorm uses net.*. Preserve every
    # other field: unexpected extra buffers must fail rather than disappear.
    return {(name[6:] if name.startswith('model.') else name[4:] if name.startswith('net.') else name): value
            for name, value in body.items()}


def state_compare(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    import torch
    left, right = core_state(left), core_state(right)
    same_keys = set(left) == set(right)
    exact = close = same_keys
    integer_equal = True
    maximum = 0.0
    for key in set(left) & set(right):
        a, b = left[key], right[key]
        if a.shape != b.shape or a.dtype != b.dtype:
            exact = close = False
            continue
        equal = torch.equal(a, b)
        exact &= equal
        if a.is_floating_point():
            maximum = max(maximum, float((a - b).abs().max()))
            close &= bool(torch.allclose(a, b, atol=1e-6, rtol=1e-5))
        else:
            integer_equal &= equal
            close &= equal
    return {'keys_equal': same_keys, 'exact': bool(exact), 'integers_equal': bool(integer_equal),
            'maximum_absolute_difference': maximum, 'within_gate': bool(close)}


def rng(device: str) -> dict[str, Any]:
    import torch
    return {'cpu': torch.get_rng_state().clone(),
            'cuda': torch.cuda.get_rng_state_all() if device.startswith('cuda') else []}


def rng_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    import torch
    return torch.equal(a['cpu'], b['cpu']) and len(a['cuda']) == len(b['cuda']) and all(
        torch.equal(x, y) for x, y in zip(a['cuda'], b['cuda']))


class Observations:
    def __init__(self) -> None:
        self.digests = {key: hashlib.sha256() for key in ('indices', 'images', 'targets', 'core_inputs')}
        self.samples = self.batches = self.indices = 0

    def index(self, index: int) -> None:
        import numpy as np
        self.digests['indices'].update(np.asarray([index], dtype=np.int64).tobytes())
        self.indices += 1

    def batch(self, images: Any, targets: Any, ids: Any = None) -> None:
        for key, tensor in (('images', images), ('targets', targets)):
            self.digests[key].update(tensor.detach().cpu().contiguous().numpy().tobytes())
        if ids is not None:
            self.digests['indices'].update(ids.detach().cpu().contiguous().numpy().tobytes())
            self.indices += len(ids)
        self.samples += len(targets)
        self.batches += 1

    def core(self, tensor: Any) -> None:
        self.digests['core_inputs'].update(tensor.detach().cpu().contiguous().numpy().tobytes())

    def result(self) -> dict[str, Any]:
        return {'samples': self.samples, 'batches': self.batches, 'recorded_indices': self.indices,
                **{key + '_sha256': digest.hexdigest() for key, digest in self.digests.items()}}


def native_data(workspace: Path, name: str, assets: Path) -> Any:
    from rpipe.structure.contracts import DataConfig
    from rpipe.structure.data.factory import DataFactory
    return DataFactory.build(DataConfig.from_mapping({'name': name, 'source': 'torch', 'config': {
        'batch_size': 250, 'test_batch_ratio': 4, 'pin_memory': True, 'num_workers': 0, 'augment': True}}),
        workspace / 'native-data', seed=SEED, origin='foreign')


def native_model(data: Any, name: str, assets: Path) -> Any:
    from rpipe.structure.contracts import ModelConfig
    from rpipe.structure.model.factory import ModelFactory
    return ModelFactory.build(ModelConfig.from_mapping({'name': name, 'source': 'custom_torch'}),
                              assets, data_meta=data.meta)


@contextlib.contextmanager
def observe_native(data: Any, module: Any):
    observations = {'train': Observations(), 'test': Observations()}
    original_iter = data.iter_batches
    def batches(split: str):
        for images, targets in original_iter(split):
            observations[split].batch(images, targets)
            yield images, targets
    base = data._train_set
    class Indexed:
        def __len__(self):
            return len(base)
        def __getitem__(self, index):
            observations['train'].index(index)
            return base[index]
    def core_hook(_module: Any, args: Any) -> None:
        observations['train' if module.training else 'test'].core(args[0])
    def restore():
        data.iter_batches = original_iter
        data._train_set = base
    with owned(data, restore):
        data.iter_batches = batches
        data._train_set = Indexed()
        handle = module.net.register_forward_pre_hook(core_hook)
        with owned(handle, handle.remove):
            yield observations


def observed_original(loader: Any, observation: Observations):
    class ObservedLoader:
        def __len__(self):
            return len(loader)

        def __iter__(self):
            for batch in loader:
                observation.batch(batch['data'], batch['target'], batch['id'])
                yield batch

    return ObservedLoader()


def mapping() -> dict[str, Any]:
    return {'source': 'custom_torch', 'mode': 'train', 'num_steps': 60, 'progress_unit': 'step',
            'eval_period': 30, 'eval_num_steps': -1, 'log_period': 30, 'checkpoint': 'latest',
            'checkpoint_period': 30, 'save_best': True, 'best_metric': 'Loss', 'best_mode': 'min',
            'best_split': 'test', 'optimizer': 'SGD', 'lr': .1, 'momentum': .9,
            'nesterov': True, 'weight_decay': .0005, 'max_grad_norm': 0.,
            'scheduler': 'cosine', 'T_max': 60, 'eta_min': 0., 'resume': False}


def run_pair(workspace: Path, device: str, pair: tuple[str, str], native: tuple[Any, Any, Any, Any, Any]) -> dict[str, Any]:
    bootstrap()
    import torch
    from rpipe.structure.contracts import AlgorithmConfig
    from rpipe.structure.algorithm.train import TrainAlgorithm
    from rpipe.structure.algorithm.evaluation import EvalAlgorithm
    from rpipe.structure.algorithm.tracker import AlgorithmTracker
    from rpipe.structure.system.factory import SystemFactory
    prepared = json.loads((workspace / 'PREPARED.json').read_text(encoding='utf-8'))
    if prepared.get('passed') is not True:
        raise ValueError('CPU preparation must pass before training')
    source_before = source_manifest()
    if json.loads((workspace / 'SOURCE_MANIFEST.json').read_text(encoding='utf-8'))['files'] != source_before:
        raise ValueError('source changed after preparation; create a new workspace')
    run_root = workspace / 'matrix'
    run_root.mkdir(exist_ok=False)
    archive = MainArchive(workspace)
    pairs = [pair]
    if len(pairs) != len(set(pairs)) or any(name not in DATA or model not in MODELS for name, model in pairs):
        raise ValueError('combinations must contain unique supported data/model pairs')
    result: dict[str, Any] = {'started_at_utc': datetime.now(timezone.utc).isoformat(), 'main_commit': MAIN,
        'dev_commit': subprocess.check_output(['git', '-c', 'safe.directory=' + str(ROOT), 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), 'device': device, 'torch': torch.__version__, 'seed': 0,
        'expected_combinations': pairs, 'full_eight_combination_matrix_selected': len(pairs) == 8,
        'scope': 'full_matrix' if len(pairs) == 8 else 'diagnostic_subset',
        'recipe': mapping(), 'control_changed_from_main_default': ['deterministic=true', 'benchmark=false', 'CUBLAS_WORKSPACE_CONFIG=:4096:8', 'cudnn_allow_tf32=false', 'matmul_allow_tf32=false'],
        'gate': {'loss_atol': 1e-6, 'parameter_atol': 1e-6, 'parameter_rtol': 1e-5,
                 'integers_and_correct_sample_counts': 'equal'}, 'runs': [], 'complete': False, 'passed': None}
    result.update(selected_complete=False, selected_passed=None, full_matrix_complete=False, full_matrix_passed=None,
                  source_manifest_sha256=sha(workspace / 'SOURCE_MANIFEST.json'),
                  archive_before=archive.verify_exports())
    environment = json.loads((workspace / 'ENVIRONMENT.json').read_text(encoding='utf-8'))
    environment['execution_device'] = device
    environment['gpu_execution_started'] = device.startswith('cuda')
    if device.startswith('cuda'):
        properties = torch.cuda.get_device_properties(torch.device(device))
        environment['gpu'] = {'name': properties.name, 'total_memory_bytes': properties.total_memory}
    save_json(workspace / 'ENVIRONMENT.json', environment)

    class CapturedTrain(TrainAlgorithm):
        def __init__(self, config: Any):
            super().__init__(config)
            self.snapshots: dict[int, Any] = {}
        def on_checkpoint(self, tracker: Any, logger: Any, data: Any, model: Any, system: Any, extra: Any = None):
            super().on_checkpoint(tracker, logger, data, model, system, extra)
            extra = extra or {}
            step = int(extra.get('step', 0))
            if step in (30, 60):
                self.snapshots[step] = {'step': step, 'model': model_state(model.module), 'scheduler': copy.deepcopy(extra['payload']['scheduler']),
                                       'optimizer': cpu_tree(extra['payload']['optimizer']),
                                       'rng': rng(device), 'train': tracker.segment_mean('train'), 'test': tracker.segment_mean('test'),
                                       'observations': {split: item.result() for split, item in self.observations.items()}}
                torch.save(self.snapshots[step], cell / f'current_step_{step}.pt')

    save_json(workspace / 'EXECUTION.json', result)
    for name in DATA:
        for model_name in MODELS:
            if (name, model_name) not in pairs:
                continue
            cell = run_root / f'{name}_{model_name}'
            cell.mkdir()
            cell_start = time.monotonic()
            if device.startswith('cuda'):
                torch.cuda.reset_peak_memory_stats(torch.device(device))
            archive.configure(name, model_name, device)
            seed_runtime(device)
            datasets = archive.datasets(name)
            old = archive.model.make_model(archive.config.cfg['model']).to(device)
            old_init, old_init_rng = model_state(old), rng(device)
            optimizer = archive.model.make_optimizer(old.parameters(), **archive.config.cfg[archive.config.cfg['tag']]['optimizer'])
            scheduler = archive.model.make_scheduler(optimizer, archive.config.cfg[archive.config.cfg['tag']]['optimizer'])
            loaders = archive.dataset.make_data_loader(datasets, {'train': 250, 'test': 1000}, 60, seed=0)
            obs_old = {'train': Observations(), 'test': Observations()}
            def old_core_hook(_module: Any, args: Any):
                obs_old['train' if old.training else 'test'].core(args[0])
            old_hook = old.model.register_forward_pre_hook(old_core_hook)
            with owned(old_hook, old_hook.remove):
                iterator = enumerate(observed_original(loaders['train'], obs_old['train']))
                logger = archive.metric.make_logger(str(cell / 'original_tensorboard'), **archive.config.cfg['log'],
                                                   tag=archive.config.cfg['tag'], metric=archive.config.cfg['metric'])
                with owned(logger.writer):
                    old_snaps = {}
                    old_best = None
                    for step in (30, 60):
                        archive.train_model.train(iterator, old, optimizer, scheduler, logger)
                        if archive.config.cfg['step'] != step:
                            raise ValueError(f'original train stopped at {archive.config.cfg["step"]}, expected {step}')
                        archive.train_model.test(observed_original(loaders['test'], obs_old['test']), old, logger)
                        snapshot = {'step': step, 'model': model_state(old), 'optimizer': cpu_tree(optimizer.state_dict()),
                                    'scheduler': copy.deepcopy(scheduler.state_dict()), 'rng': rng(device),
                                    'observations': {split: item.result() for split, item in obs_old.items()},
                                    'train': {key.split('/')[1]: value for key, value in logger.mean.items() if key.startswith('train/')},
                                    'test': {key.split('/')[1]: value for key, value in logger.mean.items() if key.startswith('test/')}}
                        old_snaps[step] = snapshot
                        torch.save(snapshot, cell / f'original_step_{step}.pt')
                        if logger.compare('test'):
                            old_best = snapshot
                        logger.reset()
            if old_best is None:
                raise ValueError('original Loss-best was not selected')
            torch.save(old_best, cell / 'original_best.pt')
            del old, optimizer, scheduler
            release_memory(device)

            archive.configure(name, model_name, device)
            config = seed_runtime(device)
            assets = cell / 'current_train' / 'assets'
            data, model, system, tracker, initial_rng = native
            torch.set_rng_state(initial_rng['cpu'])
            if device.startswith('cuda'):
                torch.cuda.set_rng_state_all(initial_rng['cuda'])
            current_init = model_state(model.module)
            current_init_rng = rng(device)
            with observe_native(data, model.module) as obs_new:
                algorithm = CapturedTrain(AlgorithmConfig.from_mapping(mapping()))
                algorithm.observations = obs_new
                summary = algorithm.run(data, model, system, tracker)
            new_best = cpu_tree(system.load_checkpoint('best'))
            current_best_path = str(system.checkpoint_dir() / 'best.pt')
            del model, data, system, tracker
            release_memory(device)
            # Original independent eval reconstructs the model/DataLoader and
            # invokes the archive's untouched test_model.test on original best.
            archive.configure(name, model_name, device)
            archive.dataset.process_dataset(datasets)
            seed_runtime(device)
            eval_old = archive.model.make_model(archive.config.cfg['model']).to(device)
            eval_old.load_state_dict(old_best['model'])
            archive.config.cfg['step'] = old_best['step']
            eval_loaders = archive.dataset.make_data_loader(datasets, {'train': 250, 'test': 1000})
            eval_logger = archive.metric.make_logger(str(cell / 'original_eval_tensorboard'), **archive.config.cfg['log'],
                                                     tag=archive.config.cfg['tag'], metric=archive.config.cfg['metric'])
            with owned(eval_logger.writer):
                old_eval_observation = Observations()
                archive.test_model.test(observed_original(eval_loaders['test'], old_eval_observation), eval_old, eval_logger)
                old_eval = {key.split('/')[1]: value for key, value in eval_logger.mean.items() if key.startswith('test/')}
            config = seed_runtime(device)
            eval_assets = cell / 'current_eval' / 'assets'
            with contextlib.ExitStack() as resources:
                eval_data = resources.enter_context(owned(native_data(workspace, name, eval_assets)))
                eval_model = resources.enter_context(owned(native_model(eval_data, model_name, eval_assets)))
                eval_observations = resources.enter_context(observe_native(eval_data, eval_model.module))
                eval_system = SystemFactory.build(config, eval_assets)
                resources.enter_context(owned(eval_system.logger))
                eval_tracker = AlgorithmTracker(eval_assets)
                eval_mapping = mapping()
                eval_mapping.update(mode='eval', resume_from=current_best_path)
                eval_summary = EvalAlgorithm(AlgorithmConfig.from_mapping(eval_mapping)).run(eval_data, eval_model, eval_system, eval_tracker)
                new_eval = eval_tracker.segment_mean('test')
                raw = {'old_init': old_init, 'old_init_rng': old_init_rng,
                    'current_init': current_init, 'current_init_rng': current_init_rng,
                    'old_snaps': old_snaps, 'current_snaps': algorithm.snapshots,
                    'old_best': old_best, 'new_best': new_best,
                    'eval_model_state': model_state(eval_model.module),
                    'old_eval': old_eval, 'new_eval': new_eval,
                    'old_eval_observation': old_eval_observation.result(),
                    'eval_observations': {key: item.result() for key, item in eval_observations.items()},
                    'obs_old': {key: item.result() for key, item in obs_old.items()},
                    'obs_new': {key: item.result() for key, item in obs_new.items()},
                    'summary': summary, 'eval_summary': eval_summary,
                    'elapsed_seconds': time.monotonic() - cell_start,
                    'gpu_peak_allocated_bytes': torch.cuda.max_memory_allocated(torch.device(device)) if device.startswith('cuda') else None,
                    'gpu_peak_reserved_bytes': torch.cuda.max_memory_reserved(torch.device(device)) if device.startswith('cuda') else None}
            if not eval_system.logger.diagnostics_complete:
                native[2].logger.diagnostics_complete = False
            torch.save(raw, cell / 'OBSERVATIONS.pt')
            result['runs'].append({'data': name, 'model': model_name, 'summary': summary,
                                  'elapsed_seconds': raw['elapsed_seconds'],
                                  'observations': str(cell / 'OBSERVATIONS.pt')})
            save_json(workspace / 'EXECUTION.json', result)
            del algorithm, eval_model, eval_data, eval_system, eval_tracker, eval_old, new_best
            release_memory(device)
    result['source_unchanged'] = source_before == source_manifest()
    result['archive_after'] = archive.verify_exports()
    result['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    save_json(workspace / 'EXECUTION.json', result)
    return result

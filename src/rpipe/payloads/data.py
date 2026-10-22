"""Scientific construction and runtime support; no orchestration imports."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator, Callable
from rpipe.structure.contracts import DataConfig

from rpipe.structure.artifacts import cache_lock, reference_tree

from rpipe.payloads.origin import apply_model_origin, bind_endpoint, discard_bad_archive, endpoint_for


class Data:
    def __init__(
        self,
        *,
        name: str,
        source: str,
        loaders: dict[str, Any],
        meta: dict[str, Any],
    ) -> None:
        self.name = name
        self.source = source
        self._loaders = loaders
        self.meta = meta
        self._train_set = None
        self._train_start = None
        self._resume_data = None
        self._iterators = {}

    def iter_batches(self, split: str) -> Iterator[Any]:
        loader = self._loaders.get(split)
        if loader is None:
            return iter(())
        if split == 'train':
            from rpipe.payloads.data_resume import (
                BatchCursor, cursor_loader, generator_states, loader_spec, restore_generators,
            )
            if loader_spec(loader) is not None:
                import torch

                if not isinstance(loader.batch_sampler, BatchCursor):
                    loader = cursor_loader(loader)
                    self._loaders[split] = loader
                cursor = loader.batch_sampler
                pending = self._resume_data
                self._resume_data = None
                inside = pending is not None and not pending['exhausted']
                if pending is not None:
                    restore_generators(loader, pending['after'] if pending['exhausted'] else pending['start'])
                cursor.consumed = pending['consumed'] if inside else 0
                cursor.exhausted = False
                self._train_start = generator_states(loader)
                # A replacement iterator must not consume another global base-seed draw.
                rng = torch.get_rng_state() if inside else None
                iterator = iter(loader)
                if rng is not None:
                    torch.set_rng_state(rng)
                self._iterators[split] = iterator
                return iterator
        iterator = iter(loader)
        self._iterators[split] = iterator
        return iterator

    def close(self) -> None:
        errors = []
        for iterator in self._iterators.values():
            close = getattr(iterator, 'close', None) or getattr(iterator, '_shutdown_workers', None)
            if callable(close):
                try:
                    close()
                except Exception as error:
                    errors.append(error)
        self._iterators.clear()
        self._loaders.clear()
        self._train_set = None
        if errors:
            error = RuntimeError('data iterator cleanup failed: ' + '; '.join(str(item) for item in errors))
            error.errors = tuple(errors)
            raise error from errors[0]

    def training_state(self) -> dict[str, Any] | None:
        from rpipe.payloads.data_resume import BatchCursor, generator_states, loader_spec

        loader = self._loaders.get('train')
        spec = loader_spec(loader)
        if spec is None or not isinstance(loader.batch_sampler, BatchCursor) or self._train_start is None:
            return None
        cursor = loader.batch_sampler
        return {'version': 1, 'spec': spec, 'seed': self.meta.get('seed'),
                'identity': {'name': self.name, 'source': self.source, 'config': self.meta.get('config')},
                'consumed': cursor.consumed, 'exhausted': cursor.exhausted,
                'start': self._train_start, 'after': generator_states(loader)}

    def restore_training_state(self, state: dict[str, Any]) -> bool:
        from rpipe.payloads.data_resume import loader_spec

        loader = self._loaders.get('train')
        if state.get('version') != 1 or state.get('spec') != loader_spec(loader) or state.get('seed') != self.meta.get('seed'):
            raise ValueError('checkpoint data sampler/batch/seed does not match current data')
        if state.get('identity') != {'name': self.name, 'source': self.source, 'config': self.meta.get('config')}:
            raise ValueError('checkpoint data identity/config does not match current data')
        consumed = state.get('consumed')
        if type(consumed) is not int or not 0 <= consumed <= len(loader.batch_sampler) or type(state.get('exhausted')) is not bool:
            raise ValueError('invalid checkpoint data cursor')
        self._resume_data = state
        return not state['exhausted']

    def steps_per_epoch(self, split: str = 'train') -> int | None:
        loader = self._loaders.get(split)
        if loader is not None:
            try:
                value = int(len(loader))
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass
        if split != 'train':
            return None
        train_size = self.meta.get('train_size')
        batch_size = self.meta.get('batch_size')
        if train_size and batch_size:
            import math

            return max(int(math.ceil(int(train_size) / int(batch_size))), 1)
        return None

    def rebind_train_steps(
        self,
        *,
        step: int,
        num_steps: int,
        step_period: int = 1,
    ) -> None:
        """Rebuild train loader for remaining optimizer steps (main ``make_data_loader``).

        ``num_samples = batch_size * (num_steps - step) * step_period``. Generator is
        re-seeded from ``meta['seed']`` each time, so a mid-run resume sees the
        **prefix** of the same shuffle, not the unseen suffix (same as main).
        Native exact resume instead binds the original full budget at step 0,
        then applies ``restore_training_state`` to select the unseen suffix.
        """
        dataset = self._train_set
        if dataset is None:
            return
        batch_size = int(self.meta.get('batch_size') or getattr(self._loaders.get('train'), 'batch_size', 1) or 1)
        period = max(int(step_period), 1)
        remaining = max(int(num_steps) - int(step), 0)
        num_samples = batch_size * remaining * period
        opts = _loader_options(self.meta.get('config') if isinstance(self.meta.get('config'), dict) else {})
        self._loaders['train'] = _train_loader_for_samples(
            dataset,
            batch_size=batch_size,
            num_samples=num_samples,
            seed=self.meta.get('seed'),
            pin_memory=opts['pin_memory'],
            num_workers=opts['num_workers'],
        )

    def to_result_snapshot(self) -> dict[str, Any]:
        out = {'name': self.name, 'source': self.source, **self.meta}
        out.pop('train_loader', None)
        out.pop('test_loader', None)
        return out

def _loader_options(cfg: dict[str, Any]) -> dict[str, Any]:
    pin = cfg.get('pin_memory')
    workers = cfg.get('num_workers', 0)
    return {
        'pin_memory': bool(pin) if pin is not None else False,
        'num_workers': int(workers or 0),
    }

def _empty_train_loader(dataset: Any, batch_size: int) -> Any:
    class _Empty:
        def __len__(self) -> int:
            return 0

        def __iter__(self):
            return iter(())

    loader = _Empty()
    loader.dataset = dataset
    loader.batch_size = batch_size
    return loader

def _train_loader_for_samples(
    dataset: Any,
    *,
    batch_size: int,
    num_samples: int,
    seed: int | None,
    pin_memory: bool = False,
    num_workers: int = 0,
) -> Any:
    from torch.utils.data import DataLoader, RandomSampler

    from rpipe.payloads.runtime import make_generator, worker_init_fn

    if num_samples <= 0:
        return _empty_train_loader(dataset, batch_size)
    generator = make_generator(seed)
    sampler = RandomSampler(
        dataset,
        replacement=False,
        num_samples=int(num_samples),
        generator=generator,
    )
    return DataLoader(
        dataset,
        batch_size=int(batch_size),
        sampler=sampler,
        pin_memory=bool(pin_memory),
        num_workers=int(num_workers),
        worker_init_fn=worker_init_fn if seed is not None else None,
    )

class DataRegistry:
    _items: dict[tuple[str, str], Callable[..., Data]] = {}

    @classmethod
    def register(cls, name: str, source: str, builder: Callable[..., Data]) -> None:
        cls._items[(name, source)] = builder

    @classmethod
    def get(cls, name: str, source: str) -> Callable[..., Data] | None:
        return cls._items.get((name, source))

    @classmethod
    def list(cls) -> list[tuple[str, str]]:
        return sorted(cls._items)

class DataFactory:
    @staticmethod
    def build(
        data_config: DataConfig,
        assets_dir: Path | str,
        seed: int | None = None,
        origin: str | None = None,
        *,
        profile_path: Path | str | None = None,
    ) -> Data:
        name = data_config.name
        source = data_config.source if data_config.source is not None else 'torch'
        builder = DataRegistry.get(name, source)
        if builder is None:
            raise ValueError(f'unknown data name/source: {name!r}/{source!r}')
        if origin not in (None, ''):
            apply_model_origin(origin)
        with cache_lock(assets_dir):
            if builder is _build_torch_vision:
                return _build_torch_vision(data_config, Path(assets_dir), seed=seed, origin=origin,
                                           profile_path=profile_path)
            return builder(data_config, Path(assets_dir), seed=seed)

def _build_stub(data_config: DataConfig, assets_dir: Path, seed: int | None = None) -> Data:
    name = data_config.name or 'unknown'
    source = data_config.source or 'stub'
    cfg = dict(data_config.config)
    return Data(
        name=name,
        source=source,
        loaders={},
        meta={
            'ready': True,
            'assets_dir': str(assets_dir),
            'config': cfg,
            'stub': True,
            'seed': seed,
        },
    )

_VISION_TORCH: dict[str, dict[str, Any]] = {
    name: {
        'root': name.lower(),
        'ctor': name,
        'train_kw': {'split': 'train'} if name == 'SVHN' else {'train': True},
        'test_kw': {'split': 'test'} if name == 'SVHN' else {'train': False},
        'data_size': shape,
        'target_size': classes,
        'mean': mean,
        'std': std,
        'train_aug': augmentation,
    }
    for name, shape, classes, mean, std, augmentation in (
        ('MNIST', (1, 28, 28), 10, (0.1307,), (0.3081,), None),
        ('FashionMNIST', (1, 28, 28), 10, (0.2860,), (0.3530,), None),
        ('CIFAR10', (3, 32, 32), 10, (0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010), 'cifar'),
        ('CIFAR100', (3, 32, 32), 100, (0.5071, 0.4867, 0.4408), (0.2675, 0.2565, 0.2761), 'cifar'),
        ('SVHN', (3, 32, 32), 10, (0.4377, 0.4438, 0.4728), (0.1980, 0.2010, 0.1970), 'svhn'),
    )
}

def _with_recorded_stats(spec: dict[str, Any], path: Path) -> dict[str, Any]:
    """Prefer mean / std from ``stats.yaml`` when a profile has been written."""
    from rpipe.payloads.data_profile import load_stats

    recorded = load_stats(path)
    if recorded is None:
        return spec
    copied = dict(spec)
    copied['mean'] = tuple(float(x) for x in recorded['mean'])
    copied['std'] = tuple(float(x) for x in recorded['std'])
    return copied

def vision_root(name: str) -> str | None:
    spec = _VISION_TORCH.get(name)
    if spec is None:
        return None
    return str(spec['root'])

def _build_torch_vision(
    data_config: DataConfig,
    assets_dir: Path,
    seed: int | None = None,
    origin: str | None = None,
    profile_path: Path | str | None = None,
) -> Data:
    import torch
    from torch.utils.data import DataLoader, Subset

    from rpipe.payloads.runtime import make_generator, worker_init_fn
    from rpipe.payloads.torchvision_load import import_torchvision

    import_torchvision()
    from torchvision import datasets, transforms

    name = data_config.name or 'unknown'
    spec = _VISION_TORCH[name]
    cfg = dict(data_config.config)
    explicit_root = data_config.path is not None
    root = Path(data_config.path) if explicit_root else assets_dir / str(spec['root'])
    if explicit_root:
        if not root.is_dir():
            raise FileNotFoundError(f'missing declared data directory: {root}')
    else:
        root.mkdir(parents=True, exist_ok=True)
    canonical_raw = assets_dir / name / 'raw'
    native_raw = root / name / 'raw' if name in ('MNIST', 'FashionMNIST') else root
    if not explicit_root and canonical_raw.is_dir():
        reference_tree(canonical_raw, native_raw)
    ctor = getattr(datasets, str(spec['ctor']))
    augment = bool(cfg.get('augment', spec.get('train_aug') is not None))
    stats = cfg.get('stats_path', profile_path if profile_path is not None else root / 'stats.yaml')
    spec = _with_recorded_stats(spec, Path(stats))
    # Pixels stay in 0–1; model-side kornia owns augmentation.
    train_tf = transforms.ToTensor()
    test_tf = transforms.ToTensor()
    endpoint = endpoint_for(name, origin)
    download = cfg.get('download', False)
    if type(download) is not bool:
        raise ValueError('data.config.download must be a boolean')
    if explicit_root and download:
        raise ValueError('declared data paths are read-only; download into the owned shared cache')
    if download:
        discard_bad_archive(root, endpoint)
    with bind_endpoint(ctor, endpoint):
        train_full = ctor(root=str(root), download=download, transform=train_tf, **dict(spec['train_kw']))
        test_ds = ctor(root=str(root), download=download, transform=test_tf, **dict(spec['test_kw']))
    if not explicit_root:
        reference_tree(native_raw, canonical_raw)
    train_size = cfg.get('train_size')
    if train_size is not None:
        n = min(int(train_size), len(train_full))
        train_ds = Subset(train_full, list(range(n)))
    else:
        n = len(train_full)
        train_ds = train_full
    batch_size = int(cfg.get('batch_size', 64))
    if batch_size > n:
        batch_size = n
    generator = make_generator(seed)
    opts = _loader_options(cfg)
    train_loader = DataLoader(
        train_ds,
        batch_size=batch_size,
        shuffle=True,
        generator=generator,
        pin_memory=opts['pin_memory'],
        num_workers=opts['num_workers'],
        worker_init_fn=worker_init_fn if seed is not None else None,
    )
    test_ratio = float(cfg.get('test_batch_ratio', 1) or 1)
    test_batch_size = max(int(batch_size * test_ratio), 1)
    test_loader = DataLoader(
        test_ds,
        batch_size=test_batch_size,
        shuffle=False,
        pin_memory=opts['pin_memory'],
        num_workers=opts['num_workers'],
    )
    data = Data(
        name=name,
        source='torch',
        loaders={'train': train_loader, 'test': test_loader},
        meta={
            'ready': True,
            'assets_dir': str(assets_dir),
            'config': cfg,
            'train_size': n,
            'test_size': len(test_ds),
            'batch_size': batch_size,
            'test_batch_size': test_batch_size,
            'data_size': list(spec['data_size']),
            'target_size': int(spec['target_size']),
            'mean': tuple(spec['mean']),
            'std': tuple(spec['std']),
            'train_aug': spec.get('train_aug') if augment else None,
            'augment': augment,
            'pin_memory': opts['pin_memory'],
            'num_workers': opts['num_workers'],
            'dtype': str(torch.float32),
            'seed': seed,
        },
    )
    data._train_set = train_ds
    return data

for _name in ('Toy', *_VISION_TORCH):
    DataRegistry.register(_name, 'stub', _build_stub)

for _name in _VISION_TORCH:
    DataRegistry.register(_name, 'torch', _build_torch_vision)

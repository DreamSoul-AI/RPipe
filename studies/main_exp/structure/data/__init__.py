"""Historical Data adapter registered through the existing DataRegistry."""
from pathlib import Path
from typing import Any
from ..api import COMMIT, SOURCE, DATA_NAMES, Observation


def register(archive, data_root, *, seed, sampler_steps):
    import numpy as np
    import torch
    from rpipe.structure.data.factory import Data, DataRegistry

    register_seed = int(seed)

    class HistoricalData(Data):
        def __init__(self, *, name: str, datasets: dict[str, Any], config: dict[str, Any], run_seed: int,
                     assets_dir: Path) -> None:
            self.datasets = datasets
            self.observations: dict[str, Observation] = {}
            self._run_seed = run_seed
            super().__init__(name=name, source=SOURCE, loaders={}, meta={
                'ready': True, 'assets_dir': str(assets_dir), 'config': config,
                'seed': run_seed, 'train_size': len(datasets['train']), 'test_size': len(datasets['test']),
                'batch_size': 250, 'test_batch_size': 250,
                'data_size': [1, 28, 28] if name == 'MNIST' else [3, 32, 32], 'target_size': 10,
                'historical_commit': COMMIT, 'sampler_steps': sampler_steps,
                # Normalization is already in the original dataset transform.
                'normalization_in_dataset': True,
            })
            self._loaders['test'] = torch.utils.data.DataLoader(
                datasets['test'], batch_size=250, shuffle=False, pin_memory=True,
                num_workers=0, collate_fn=archive.dataset.input_collate,
                worker_init_fn=np.random.seed(run_seed),
            )
            self.rebind_train_steps(step=0, num_steps=sampler_steps)

        def rebind_train_steps(self, *, step: int, num_steps: int, step_period: int = 1) -> None:
            del num_steps
            if step != 0 or step_period != 1:
                raise ValueError('exact historical runs require an uninterrupted start at step 0, step_period 1')
            generator = torch.Generator().manual_seed(self._run_seed)
            sampler = torch.utils.data.RandomSampler(
                self.datasets['train'], replacement=False, num_samples=250 * sampler_steps,
                generator=generator,
            )
            self._loaders['train'] = torch.utils.data.DataLoader(
                self.datasets['train'], batch_size=250, sampler=sampler, pin_memory=True,
                num_workers=0, collate_fn=archive.dataset.input_collate,
                worker_init_fn=np.random.seed(self._run_seed),
            )

        def iter_batches(self, split: str, *, resumable: bool = True):
            if type(resumable) is not bool:
                raise ValueError('resumable must be a boolean')
            # Pinned loaders have no cursor snapshot; training_state stays unsupported.
            iterator = iter(self._loaders[split])
            self._iterators[split] = iterator
            for batch in iterator:
                if split in self.observations:
                    self.observations[split].add(batch)
                yield batch['data'], batch['target']

    def build_data(data_config: Any, assets_dir: Path, seed: int | None = None):
        run_seed = int(seed if seed is not None else register_seed)
        cfg = dict(data_config.config)
        if int(cfg.get('batch_size', 250)) != 250 or float(cfg.get('test_batch_ratio', 1)) != 1:
            raise ValueError('historical batch sizes must be train250/test250')
        if cfg.get('train_size') is not None or int(cfg.get('num_workers', 0)) != 0:
            raise ValueError('historical recipe uses full datasets and num_workers=0')
        if cfg.get('pin_memory', True) is not True:
            raise ValueError('historical recipe requires pin_memory=true')
        archive.configure(data_config.name, 'linear', run_seed)
        datasets = archive.datasets(data_root, data_config.name)
        return HistoricalData(name=data_config.name, datasets=datasets, config=cfg,
                              run_seed=run_seed, assets_dir=assets_dir)


    for name in DATA_NAMES:
        DataRegistry.register(name, SOURCE, build_data)

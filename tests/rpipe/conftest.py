"""Small source declarations and payloads shared by RPipe contract tests."""

from pathlib import Path

import pytest


@pytest.fixture
def source_study(tmp_path):
    source = tmp_path / 'source' / 'study'
    source.mkdir(parents=True)
    (source / 'study.yaml').write_text('study: toy\nseeds: [0, 1]\n', encoding='utf-8')
    (source / 'experiment_config.yaml').write_text(
        'experiment: toy\ndata: {name: Toy, source: stub}\nmodel: {name: linear}\n'
        'algorithm: {mode: train, num_steps: 2}\nsystem: {device: cpu}\n', encoding='utf-8')
    return source


@pytest.fixture
def run_root(tmp_path):
    return tmp_path / 'artifacts'


@pytest.fixture
def payload_factory():
    class Payload:
        def __init__(self, *, fail=None, close_error=None):
            self.calls = []
            self.fail = fail
            self.close_error = close_error

        def _phase(self, phase):
            self.calls.append(phase)
            if self.fail == phase:
                raise ValueError(f'{phase} primary failure')

        def prepare(self, ctx):
            self._phase('prepare')

        def execute(self, ctx):
            self._phase('execute')
            return {'value': 3.0}

        def collect(self, ctx):
            self._phase('collect')
            return {'metrics': {'value': 3.0}}

        def close(self, ctx):
            self.calls.append('close')
            if self.close_error:
                raise RuntimeError(self.close_error)

    return Payload


@pytest.fixture
def file_bytes():
    def snapshot(root: Path):
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in root.rglob('*') if p.is_file()}
    return snapshot


@pytest.fixture
def tensor_study(source_study):
    """CPU float32: 8 two-feature samples, linear 6-parameter model, 2 updates, seed 7."""
    (source_study / 'study.yaml').write_text(
        'study: tensor_cpu\nseeds: [7]\nrecipe: recipe.py\naxes: {algorithm.mode: [train, eval]}\n',
        encoding='utf-8')
    (source_study / 'experiment_config.yaml').write_text(
        'experiment: tensor_cpu\ndata: {name: TinyCPU, source: fixture}\n'
        'model: {name: linear, source: custom_torch}\n'
        'algorithm: {source: custom_torch, num_steps: 2, lr: 0.05, save_best: true, '
        'eval_period: 1, checkpoint: latest}\nsystem: {device: cpu, deterministic: true}\n', encoding='utf-8')
    (source_study / 'recipe.py').write_text('''from rpipe.structure.data.factory import Data, DataRegistry
from rpipe.structure.system.runtime import make_generator

def register(ctx):
    def build(config, assets, seed=None):
        import torch
        from torch.utils.data import DataLoader, TensorDataset
        x = torch.arange(16, dtype=torch.float32).reshape(8, 2) / 16
        y = (torch.arange(8) % 2).long()
        dataset = TensorDataset(x, y)
        return Data(name='TinyCPU', source='fixture', loaders={
            'train': DataLoader(dataset, batch_size=4, shuffle=True, generator=make_generator(seed)),
            'test': DataLoader(dataset, batch_size=4)}, meta={
                'seed': seed, 'config': dict(config.config), 'data_size': [2],
                'target_size': 2, 'batch_size': 4, 'train_size': 8})
    DataRegistry.register('TinyCPU', 'fixture', build)
''', encoding='utf-8')
    return source_study

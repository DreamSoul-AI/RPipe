from pathlib import Path

import pytest

from rpipe.structure.contracts import AlgorithmConfig
from rpipe.structure.algorithm.tracker import AlgorithmTracker
from rpipe.structure.algorithm.train import TrainAlgorithm
from rpipe.structure.contracts import SystemConfig
from rpipe.structure.system.factory import SystemFactory

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.structure_layer,
    pytest.mark.module_algorithm,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]


def _run(tmp_path: Path, mapping: dict) -> tuple[dict, Path]:
    import torch

    class _Data:
        name = 'CIFAR'
        meta = {'train_size': 8, 'batch_size': 4}

        def iter_batches(self, split: str):
            del split
            images = torch.randn(4, 1, 28, 28)
            targets = torch.zeros(4, dtype=torch.long)
            yield images, targets
            yield images, targets

    class _Model:
        def __init__(self) -> None:
            self.module = torch.nn.Linear(784, 10)

    algo = TrainAlgorithm(AlgorithmConfig.from_mapping(mapping))
    tracker = AlgorithmTracker(tmp_path)
    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    out = algo.run(_Data(), _Model(), system, tracker)
    return out, tmp_path / 'checkpoints'


def test_epoch_train_writes_latest_only_by_default(tmp_path: Path):
    out, ckpt = _run(tmp_path, {'mode': 'train', 'num_epochs': 2, 'eval_period': 1})
    assert out['epochs'] == 2
    assert out['progress_unit'] == 'step'
    assert out['steps'] == 4
    assert (ckpt / 'latest.pt').is_file()
    assert (ckpt / 'latest' / 'model.pt').is_file()
    assert (ckpt / 'latest' / 'optimizer.pt').is_file()
    assert (ckpt / 'latest' / 'tracker.json').is_file()
    assert not (ckpt / 'best.pt').is_file()
    assert list(ckpt.glob('epoch_*.pt')) == []


def test_save_best_writes_best_pt(tmp_path: Path):
    out, ckpt = _run(
        tmp_path,
        {'mode': 'train', 'num_epochs': 2, 'eval_period': 1, 'save_best': True},
    )
    assert out['best_accuracy'] is not None
    assert (ckpt / 'latest.pt').is_file()
    assert (ckpt / 'best.pt').is_file()


def test_percent_keeps_named_epoch_snapshots(tmp_path: Path):
    _out, ckpt = _run(
        tmp_path,
        {
            'mode': 'train',
            'num_epochs': 4,
            'eval_period': 1,
            'checkpoint': 'percent',
            'checkpoint_percents': [0.5, 1.0],
        },
    )
    assert (ckpt / 'latest.pt').is_file()
    assert (ckpt / 'step_000004.pt').is_file()
    assert (ckpt / 'step_000008.pt').is_file()
    assert not (ckpt / 'step_000002.pt').is_file()


def test_num_steps_stops_and_names_step_snapshots(tmp_path: Path):
    out, ckpt = _run(
        tmp_path,
        {
            'mode': 'train',
            'num_steps': 3,
            'eval_period': 3,
            'checkpoint': 'percent',
            'checkpoint_percents': [1.0],
        },
    )
    assert out['progress_unit'] == 'step'
    assert out['steps'] == 3
    assert out['epochs'] == 2
    assert (ckpt / 'latest.pt').is_file()
    assert (ckpt / 'step_000003.pt').is_file()


def test_resume_restores_tracker_history(tmp_path: Path):
    mapping = {
        'mode': 'train',
        'num_epochs': 1,
        'progress_unit': 'epoch',
        'eval_period': 1,
        'checkpoint_period': 1,
    }
    _run(tmp_path, mapping)
    import json

    body = json.loads((tmp_path / 'checkpoints' / 'latest' / 'tracker.json').read_text(encoding='utf-8'))
    assert 'splits' in body
    second, _ = _run(tmp_path, mapping)
    assert second['epochs'] == 1


def test_step_period_accumulates_before_optimizer_step(tmp_path: Path):
    out, _ = _run(
        tmp_path,
        {
            'mode': 'train',
            'num_epochs': 1,
            'progress_unit': 'epoch',
            'eval_period': 1,
            'step_period': 2,
        },
    )
    assert out['steps'] == 1
    import json

    records = [json.loads(line) for line in (tmp_path / 'tracker' / 'scalars.jsonl').read_text().splitlines()]
    reports = [row for row in records if 'mean' in row]
    assert {row['optimizer_step'] for row in reports} == {1}
    assert reports[-1]['step'] > reports[-1]['optimizer_step']  # Includes test and accumulated batches.
    checkpoint = json.loads((tmp_path / 'checkpoints' / 'latest' / 'tracker.json').read_text())
    assert checkpoint['jsonl_offset'] == (tmp_path / 'tracker' / 'scalars.jsonl').stat().st_size


def test_curve_discards_reports_after_failed_checkpoint_and_resume(tmp_path, monkeypatch):
    import json
    from rpipe.structure.readout import load_curve_series
    from rpipe.structure.system.factory import System

    original = System.save_checkpoint
    failed = False

    def fail_once(system, payload, name):
        nonlocal failed
        if not failed and name == 'latest' and payload['step'] == 3:
            failed = True
            raise OSError('injected checkpoint failure')
        return original(system, payload, name)

    monkeypatch.setattr(System, 'save_checkpoint', fail_once)
    root = tmp_path / 'runs' / 'run' / 'assets'
    mapping = {'mode': 'train', 'num_steps': 4, 'eval_period': 1, 'log_period': 1}
    with pytest.raises(OSError, match='injected checkpoint failure'):
        _run(root, mapping)
    completed, _ = _run(root, mapping)
    assert completed['steps'] == 4
    reports = [json.loads(line) for line in (root / 'tracker' / 'scalars.jsonl').read_text().splitlines()]
    at3 = [row for row in reports if row.get('split') == 'test' and row.get('name') == 'Loss' and row.get('optimizer_step') == 3]
    assert len(at3) == 2  # Raw diagnostics retain the failed branch.
    curve = load_curve_series(tmp_path, 'run')['test']['Loss']
    assert curve['x'] == [1, 2, 3, 4]
    assert curve['values'][2] == at3[-1]['mean']


def test_checkpoint_curve_progress_is_current_even_without_a_report(tmp_path):
    import torch

    _run(tmp_path, {
        'num_steps': 4, 'eval_period': 0, 'checkpoint': 'percent', 'checkpoint_percents': [0.75],
    })
    snapshot = torch.load(tmp_path / 'checkpoints' / 'step_000003.pt', weights_only=False)
    assert snapshot['step'] == snapshot['tracker']['progress']['step'] == 3
    # The last reported train segment ended at step 2, but these weights are from step 3.
    assert snapshot['tracker']['splits']['train']['Loss']['history']


def test_resume_rejects_changed_native_sampler_budget(tmp_path: Path):
    import torch
    from torch.utils.data import TensorDataset

    from rpipe.structure.data.factory import Data

    images = torch.arange(32, dtype=torch.float32).view(32, 1).expand(32, 784).reshape(32, 1, 28, 28)
    labels = torch.zeros(32, dtype=torch.long)
    dataset = TensorDataset(images, labels)

    def _data() -> Data:
        data = Data(
            name='toy',
            source='x',
            loaders={},
            meta={'train_size': 32, 'batch_size': 4, 'seed': 0},
        )
        data._train_set = dataset
        return data

    class _Model:
        def __init__(self) -> None:
            self.module = torch.nn.Linear(784, 10)

    mapping_partial = {
        'mode': 'train',
        'num_steps': 2,
        'eval_period': 0,
        'checkpoint_period': 1,
        'init_seed': 0,
    }
    algo = TrainAlgorithm(AlgorithmConfig.from_mapping(mapping_partial))
    tracker = AlgorithmTracker(tmp_path)
    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    first = algo.run(_data(), _Model(), system, tracker)
    assert first['steps'] == 2
    remaining = _data()
    remaining.rebind_train_steps(step=2, num_steps=4, step_period=1)
    assert len(remaining._loaders['train']) == 2

    mapping_full = dict(mapping_partial)
    mapping_full['num_steps'] = 4
    algo2 = TrainAlgorithm(AlgorithmConfig.from_mapping(mapping_full))
    tracker2 = AlgorithmTracker(tmp_path)
    system2 = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    with pytest.raises(ValueError, match='training contract differs'):
        algo2.run(_data(), _Model(), system2, tracker2)


def test_resume_latest_skips_when_budget_already_done(tmp_path: Path):
    mapping = {
        'mode': 'train',
        'num_epochs': 1,
        'progress_unit': 'epoch',
        'eval_period': 1,
        'checkpoint': 'latest',
        'checkpoint_period': 1,
    }
    first, _ = _run(tmp_path, mapping)
    assert first['epochs'] == 1
    second, _ = _run(tmp_path, mapping)
    assert second['epochs'] == 1
    assert second['steps'] == first['steps']


def test_eval_algorithm_loads_best(tmp_path: Path):
    from rpipe.structure.algorithm.evaluation import EvalAlgorithm

    mapping = {
        'mode': 'train',
        'num_epochs': 1,
        'progress_unit': 'epoch',
        'eval_period': 1,
        'save_best': True,
    }
    trained, ckpt = _run(tmp_path, mapping)
    assert (ckpt / 'best.pt').is_file()
    import torch

    class _Data:
        name = 'CIFAR'
        meta = {'train_size': 8, 'batch_size': 4}

        def iter_batches(self, split: str):
            del split
            images = torch.randn(4, 1, 28, 28)
            targets = torch.zeros(4, dtype=torch.long)
            yield images, targets

    class _Model:
        def __init__(self) -> None:
            self.module = torch.nn.Linear(784, 10)

    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    tracker = AlgorithmTracker(tmp_path / 'eval_tracker')
    out = EvalAlgorithm(AlgorithmConfig.from_mapping({'mode': 'eval'})).run(
        _Data(), _Model(), system, tracker
    )
    assert out['mode'] == 'eval'
    assert 'accuracy' in out
    assert out['resume_stem'] == 'best'


def test_eval_algorithm_missing_checkpoint_fails(tmp_path: Path):
    from rpipe.structure.algorithm.evaluation import EvalAlgorithm

    import torch

    class _Data:
        name = 'CIFAR'
        meta = {}

        def iter_batches(self, split: str):
            del split
            yield torch.randn(2, 1, 28, 28), torch.zeros(2, dtype=torch.long)

    class _Model:
        module = torch.nn.Linear(784, 10)

    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    tracker = AlgorithmTracker(tmp_path)
    with pytest.raises(FileNotFoundError, match='eval resume missing'):
        EvalAlgorithm(AlgorithmConfig.from_mapping({'mode': 'eval'})).run(
            _Data(), _Model(), system, tracker
        )


def test_legacy_training_resume_warns_but_loads_weights(tmp_path):
    from types import SimpleNamespace
    import torch
    from rpipe.structure.algorithm.factory import Algorithm

    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    original = torch.nn.Linear(2, 2)
    system.save_checkpoint({'model': original.state_dict(), 'step': 1}, 'latest')
    model = SimpleNamespace(module=torch.nn.Linear(2, 2))
    with pytest.warns(RuntimeWarning, match='resume is partial'):
        restored = Algorithm(AlgorithmConfig.from_mapping({'mode': 'train'})).resume(
            None, model, system, AlgorithmTracker(tmp_path))
    assert restored['step'] == 1
    for key, value in model.module.state_dict().items():
        assert torch.equal(value, original.state_dict()[key])


def test_epoch_budget_stops_even_when_accumulation_drops_tail_batches(tmp_path):
    from types import SimpleNamespace
    import torch

    class Batches:
        meta = {'train_size': 10, 'batch_size': 2}
        calls = 0

        def iter_batches(self, split):
            if split != 'train':
                return
            self.calls += 1
            assert self.calls <= 3, 'unbounded epoch loop'
            for _ in range(5):
                yield torch.ones(2, 2), torch.zeros(2, dtype=torch.long)

    data = Batches()
    config = AlgorithmConfig.from_mapping({'mode': 'train', 'num_epochs': 2, 'progress_unit': 'epoch',
                                          'step_period': 2, 'eval_period': 0, 'checkpoint_period': 1})
    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    model = SimpleNamespace(module=torch.nn.Linear(2, 2))
    result = TrainAlgorithm(config).run(data, model, system, AlgorithmTracker(tmp_path))
    assert result['epochs'] == data.calls == 2
    assert result['steps'] == 4
    with pytest.warns(RuntimeWarning, match='resume is partial'):
        resumed = TrainAlgorithm(config).run(data, model, system, AlgorithmTracker(tmp_path))
    assert data.calls == 2 and resumed['epochs'] == 2 and resumed['steps'] == 4


@pytest.mark.parametrize('unit', ['step', 'epoch'])
def test_training_rejects_iterator_too_short_for_any_optimizer_update(tmp_path, unit):
    from types import SimpleNamespace
    import torch

    class Batches:
        meta = {'train_size': 4, 'batch_size': 2}
        calls = 0

        def iter_batches(self, split):
            if split != 'train':
                return
            self.calls += 1
            assert self.calls <= 3, 'unbounded no-update loop'
            for _ in range(2):
                yield torch.ones(2, 2), torch.zeros(2, dtype=torch.long)

    config = AlgorithmConfig.from_mapping({'mode': 'train', 'num_epochs': 2, 'progress_unit': unit,
                                          'step_period': 3, 'eval_period': 0})
    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    with pytest.raises(ValueError, match='fewer batches than step_period'):
        TrainAlgorithm(config).run(Batches(), SimpleNamespace(module=torch.nn.Linear(2, 2)), system, AlgorithmTracker(tmp_path))


@pytest.mark.parametrize('unit', ['step', 'epoch'])
def test_training_rejects_empty_iterator_before_successful_checkpoint(tmp_path, unit):
    from types import SimpleNamespace
    import torch

    class Empty:
        meta = {'train_size': 0, 'batch_size': 2}

        def iter_batches(self, split):
            return iter(())

    config = AlgorithmConfig.from_mapping({'mode': 'train', 'num_steps': 2, 'num_epochs': 2,
                                          'progress_unit': unit, 'eval_period': 0})
    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    with pytest.raises(ValueError, match='empty train iterator'):
        TrainAlgorithm(config).run(Empty(), SimpleNamespace(module=torch.nn.Linear(2, 2)), system, AlgorithmTracker(tmp_path))
    assert not (tmp_path / 'checkpoints/latest.pt').exists()


def test_unrecorded_device_rng_is_not_marked_as_complete_resume(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import torch
    import rpipe.structure.algorithm.train as train
    from rpipe.structure.data.factory import Data

    dataset = torch.utils.data.TensorDataset(torch.ones(2, 2), torch.zeros(2, dtype=torch.long))
    loader = torch.utils.data.DataLoader(dataset, batch_size=2)
    data = Data(name='rng-boundary', source='custom', loaders={'train': loader, 'test': loader}, meta={'seed': 0})
    real_system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    # CPU computation only: exercise the declaration guard, not real MPS execution.
    declared = SimpleNamespace(device='mps', logger=real_system.logger, place_module=lambda module: module,
                               load_checkpoint=real_system.load_checkpoint, save_checkpoint=real_system.save_checkpoint)
    monkeypatch.setattr(train, 'prepare_tensors', lambda batch, module, device: batch)
    import rpipe.structure.algorithm.evaluation_support as eval_hook
    monkeypatch.setattr(eval_hook, 'prepare_tensors', lambda batch, module, device: batch)
    config = AlgorithmConfig.from_mapping({'mode': 'train', 'num_steps': 1, 'resume': False, 'eval_period': 0})
    TrainAlgorithm(config).run(data, SimpleNamespace(module=torch.nn.Linear(2, 2)), declared, AlgorithmTracker(tmp_path))
    payload = torch.load(tmp_path / 'checkpoints/latest.pt', weights_only=False)
    assert payload['training_state']['data'] is None


def test_cnn_trains_on_cifar_shaped_batches(tmp_path: Path):
    import torch

    from rpipe.structure.model.factory import ModelFactory as model_api
    from rpipe.structure.contracts import ModelConfig

    class _Data:
        name = 'CIFAR10'
        meta = {'train_size': 8, 'batch_size': 4, 'data_size': [3, 32, 32], 'target_size': 10}

        def iter_batches(self, split: str):
            del split
            images = torch.randn(4, 3, 32, 32)
            targets = torch.zeros(4, dtype=torch.long)
            yield images, targets
            yield images, targets

    model = model_api.build(
        ModelConfig.from_mapping({'name': 'cnn'}),
        tmp_path,
        data_meta={'data_size': [3, 32, 32], 'target_size': 10},
    )
    algo = TrainAlgorithm(AlgorithmConfig.from_mapping({'mode': 'train', 'num_steps': 2, 'eval_period': 0}))
    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    out = algo.run(_Data(), model, system, AlgorithmTracker(tmp_path))
    assert out['steps'] == 2

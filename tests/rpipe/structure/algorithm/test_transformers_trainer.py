import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.external,
    pytest.mark.content,
    pytest.mark.p2,
    pytest.mark.structure_layer,
    pytest.mark.module_algorithm,
    pytest.mark.cost(cost_class='c2'),
    pytest.mark.result_type('categorical', detail='summary'),
]


from rpipe.structure.contracts import AlgorithmConfig
from rpipe.structure.algorithm.tracker import AlgorithmTracker
from rpipe.structure.contracts import SystemConfig
from rpipe.structure.system.factory import SystemFactory

@pytest.mark.parametrize('budget', [{'num_epochs': 1, 'progress_unit': 'epoch'}, {'num_steps': 1}])
def test_hf_trainer_records_actual_progress(tmp_path, budget):
    pytest.importorskip('transformers')
    pytest.importorskip('accelerate')
    import torch

    from rpipe.structure.algorithm.transformers_trainer import HfTrainAlgorithm

    train_ds = torch.utils.data.TensorDataset(
        torch.randn(8, 1, 28, 28),
        torch.zeros(8, dtype=torch.long),
    )
    test_ds = torch.utils.data.TensorDataset(
        torch.randn(4, 1, 28, 28),
        torch.zeros(4, dtype=torch.long),
    )

    class _Loader:
        def __init__(self, dataset, steps: int) -> None:
            self.dataset = dataset
            self._steps = steps

        def __len__(self) -> int:
            return self._steps

        def __iter__(self):
            for i in range(self._steps):
                x, y = self.dataset[i % len(self.dataset)]
                yield x.unsqueeze(0), y.unsqueeze(0)

    class _Data:
        name = 'CIFAR'
        meta = {'train_size': 8, 'batch_size': 4, 'seed': 0}

        def __init__(self) -> None:
            self._loaders = {'train': _Loader(train_ds, 2), 'test': _Loader(test_ds, 1)}

        def iter_batches(self, split: str):
            yield from self._loaders[split]

        def steps_per_epoch(self, split: str = 'train'):
            del split
            return 2

    class _Model:
        def __init__(self) -> None:
            self.module = torch.nn.Linear(784, 10)

    algo = HfTrainAlgorithm(
        AlgorithmConfig.from_mapping(
            {
                'mode': 'train',
                'source': 'transformers_trainer',
                **budget,
                'eval_period': 1,
                'optimizer': 'SGD',
                'lr': 0.1,
                'scheduler': 'cosine',
            }
        )
    )
    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    data, model = _Data(), _Model()
    updates = []
    model.module.weight.register_hook(lambda grad: updates.append(1))
    out = algo.run(data, model, system, AlgorithmTracker(tmp_path))
    assert out['source'] == 'transformers_trainer'
    recorded_epoch = 1 if 'num_epochs' in budget else 0.5
    assert (tmp_path / 'checkpoints' / 'latest.pt').is_file()
    import json

    reports = [json.loads(line) for line in (tmp_path / 'tracker' / 'scalars.jsonl').read_text().splitlines()]
    reports = [row for row in reports if 'mean' in row]
    assert {row['optimizer_step'] for row in reports} == {out['steps']}
    assert {row['epoch'] for row in reports} == {recorded_epoch}
    assert len(updates) == out['steps']
    if 'num_epochs' in budget:
        # Count actual backward/update calls, independently of the curve's resume offset.
        second = HfTrainAlgorithm(AlgorithmConfig.from_mapping({
            'mode': 'train', 'source': 'transformers_trainer', 'num_epochs': 2,
            'progress_unit': 'epoch', 'eval_period': 1, 'optimizer': 'SGD', 'lr': 0.1,
        }))
        second.run(data, model, system, AlgorithmTracker(tmp_path))
        checkpoint = system.load_checkpoint('latest')
        assert checkpoint['tracker']['progress']['step'] == len(updates)
        rows = [json.loads(line) for line in (tmp_path / 'tracker' / 'scalars.jsonl').read_text().splitlines()]
        assert rows[-1]['optimizer_step'] == len(updates)


@pytest.mark.parametrize('unit,eval_period,interrupt_step', [
    ('step', 1, 2), ('step', 0, 1), ('step', 3, 1), ('epoch', 1, 2),
])
def test_hf_resume_only_runs_remaining_updates_and_keeps_total_coordinates(tmp_path, unit, eval_period, interrupt_step):
    pytest.importorskip('transformers')
    pytest.importorskip('accelerate')
    import json
    from types import SimpleNamespace
    import torch
    from rpipe.structure.algorithm.transformers_trainer import HfTrainAlgorithm
    from rpipe.structure.algorithm.metrics import MetricBundle

    class Data:
        source = 'torch'
        meta = {'train_size': 2, 'batch_size': 1, 'seed': 19}

        def __init__(self):
            dataset = torch.utils.data.TensorDataset(torch.eye(2), torch.tensor([0, 1]))
            self._loaders = {split: torch.utils.data.DataLoader(dataset, batch_size=1)
                             for split in ('train', 'test')}

        def iter_batches(self, split):
            return iter(self._loaders[split])

        def steps_per_epoch(self):
            return 2

    settings = {'mode': 'train', 'source': 'transformers_trainer', 'progress_unit': unit,
                'eval_period': eval_period, 'checkpoint_period': 1, 'lr': 0.01, 'momentum': 0.0,
                'metric': {'train': ['Loss'], 'test': ['Loss']}, 'best_metric': 'Loss'}
    settings.update({'num_steps': 4} if unit == 'step' else {'num_epochs': 2})
    config = AlgorithmConfig.from_mapping(settings)
    module = torch.nn.Linear(2, 2)
    updates = []
    module.weight.register_hook(lambda gradient: updates.append(1))
    system = SystemFactory.build(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path)
    original = system.save_checkpoint

    def interrupt(payload, name):
        path = original(payload, name)
        if name == 'latest' and payload['step'] == interrupt_step:
            assert payload['tracker']['progress'] == {'step': interrupt_step, 'epoch': interrupt_step / 2}
            raise RuntimeError('owned checkpoint interruption')
        return path

    def tracker():
        return AlgorithmTracker(tmp_path, MetricBundle({'train': ['Loss'], 'test': ['Loss']}))

    try:
        system.save_checkpoint = interrupt
        with pytest.raises(RuntimeError, match='owned checkpoint interruption'):
            HfTrainAlgorithm(config).run(Data(), SimpleNamespace(module=module), system, tracker())
        assert len(updates) == interrupt_step
        system.save_checkpoint = original
        with pytest.warns(RuntimeWarning, match='resume is partial'):
            result = HfTrainAlgorithm(config).run(Data(), SimpleNamespace(module=module), system, tracker())
        assert len(updates) == result['steps'] == 4
        assert result['epochs'] == 2
        assert 'accuracy' not in result and 'best_accuracy' not in result
        latest = system.load_checkpoint('latest')
        assert latest['step'] == latest['tracker']['progress']['step'] == 4
        rows = [json.loads(line) for line in (tmp_path / 'tracker/scalars.jsonl').read_text().splitlines()]
        rows = [row for row in rows if 'mean' in row]
        assert rows[-1]['optimizer_step'] == 4
        assert rows[-1]['epoch'] == 2
    finally:
        system.logger.close()

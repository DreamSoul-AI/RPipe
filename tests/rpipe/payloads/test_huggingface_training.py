import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p2,
    pytest.mark.structure_layer,
    pytest.mark.module_algorithm,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]


from rpipe.structure.contracts import AlgorithmConfig
from rpipe.payloads.tracker import AlgorithmTracker
from rpipe.structure.contracts import SystemConfig
from rpipe.payloads.checkpoints import SystemFactory

pytest.importorskip('transformers')


@pytest.mark.parametrize('budget', [{'num_epochs': 1, 'progress_unit': 'epoch'}, {'num_steps': 1}])
def test_hf_trainer_records_actual_progress(tmp_path, budget):
    import torch

    from rpipe.payloads.huggingface_training import HfTrainAlgorithm

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

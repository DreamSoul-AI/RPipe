"""The HF adapter consumes public Data capabilities and preserves its native loader."""
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_algorithm,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_hf_batch_adapter_keeps_loader_sampler_generator_and_batch_order(monkeypatch):
    import torch
    from rpipe.structure.data.factory import Data
    from rpipe.structure.data import resume
    from rpipe.structure.algorithm.transformers_trainer import _NativeBatchLoader

    dataset = torch.utils.data.TensorDataset(torch.arange(8).reshape(4, 2), torch.arange(4))
    generator = torch.Generator().manual_seed(29)
    loader = torch.utils.data.DataLoader(dataset, batch_size=2, shuffle=True, generator=generator)
    sampler, batch_sampler = loader.sampler, loader.batch_sampler
    state = generator.get_state().clone()
    expected = list(iter(loader))
    generator.set_state(state)
    data = Data(name='fixture', source='torch', loaders={'train': loader}, meta={'batch_size': 2})
    requested = []
    class PublicData:
        @property
        def _loaders(self):
            pytest.fail('HF adapter reached private Data loader storage')
        def dataset(self, split):
            return data.dataset(split)
        def batch_size(self, split='train'):
            return data.batch_size(split)
        def steps_per_epoch(self, split='train'):
            return data.steps_per_epoch(split)
        def iter_batches(self, split, *, resumable=True):
            requested.append(resumable)
            return data.iter_batches(split, resumable=resumable)
    monkeypatch.setattr(resume, 'cursor_loader', lambda *args: pytest.fail('HF rebuilt a resume cursor loader'))
    try:
        batches = _NativeBatchLoader(PublicData())
        assert batches.dataset is dataset and batches.batch_size == 2
        assert len(batches) == len(loader)
        actual = list(batches)
        assert requested == [False]
        assert data._loaders['train'] is loader
        assert loader.sampler is sampler and loader.batch_sampler is batch_sampler
        assert loader.generator is generator
        assert len(actual) == len(expected)
        for observed, (images, labels) in zip(actual, expected):
            assert torch.equal(observed['pixel_values'], images)
            assert torch.equal(observed['labels'], labels)
    finally:
        data.close()

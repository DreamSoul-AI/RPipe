"""The historical adapter accepts the Data port without claiming cursor recovery."""
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import uuid

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_data,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('resumable', [True, False])
def test_historical_batches_keep_original_loader_and_partial_resume_semantics(
        tmp_path, monkeypatch, resumable):
    import torch
    from rpipe.structure.control import DataConfig
    from rpipe.structure.data.factory import DataRegistry
    from rpipe.structure.data import resume
    from rpipe.structure.system.runtime import capture_rng, restore_rng

    name = '_historical_data_port_' + uuid.uuid4().hex
    package = ModuleType(name)
    package.__path__ = [str(Path(__file__).resolve().parents[4] / 'studies/main_exp/structure')]
    monkeypatch.setitem(sys.modules, name, package)
    module = importlib.import_module(name + '.data')
    monkeypatch.setattr(DataRegistry, '_items', {})
    dataset = torch.utils.data.TensorDataset(torch.arange(4).reshape(4, 1), torch.arange(4))
    def collate(rows):
        return {'data': torch.stack([row[0] for row in rows]),
                'target': torch.stack([row[1] for row in rows])}
    archive = SimpleNamespace(configure=lambda *args: None,
                              datasets=lambda *args: {'train': dataset, 'test': dataset},
                              dataset=SimpleNamespace(input_collate=collate))
    original = capture_rng()
    data = None
    try:
        module.register(archive, tmp_path, seed=17, sampler_steps=1)
        builder = DataRegistry.get('MNIST', module.SOURCE)
        data = builder(DataConfig(name='MNIST', source=module.SOURCE), tmp_path)
        loader = data._loaders['train']
        sampler, generator = loader.sampler, loader.sampler.generator
        monkeypatch.setattr(resume, 'cursor_loader', lambda *args: pytest.fail('historical loader became a resume cursor'))
        batches = list(data.iter_batches('train', resumable=resumable))
        assert len(batches) == 1 and tuple(batches[0][0].shape) == (250, 1)
        assert data._loaders['train'] is loader
        assert loader.sampler is sampler and sampler.generator is generator
        assert data.training_state() is None
    finally:
        if data is not None:
            data.close()
        restore_rng(original)
        for key in tuple(sys.modules):
            if key.startswith(name + '.'):
                sys.modules.pop(key, None)

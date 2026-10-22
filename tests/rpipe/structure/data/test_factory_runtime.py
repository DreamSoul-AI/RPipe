import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p2,
    pytest.mark.structure_layer,
    pytest.mark.module_data,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]

def test_rebind_train_steps_uses_remaining_prefix():
    import torch
    from torch.utils.data import TensorDataset

    from rpipe.structure.data.factory import Data

    dataset = TensorDataset(torch.arange(20), torch.zeros(20, dtype=torch.long))
    data = Data(name='toy', source='x', loaders={}, meta={'batch_size': 2, 'seed': 0})
    data._train_set = dataset
    data.rebind_train_steps(step=0, num_steps=4, step_period=1)
    assert len(data._loaders['train']) == 4
    full = [batch[0].clone() for batch in data.iter_batches('train')]
    data.rebind_train_steps(step=2, num_steps=4, step_period=1)
    rest = [batch[0].clone() for batch in data.iter_batches('train')]
    assert len(rest) == 2
    assert torch.equal(rest[0], full[0])
    assert torch.equal(rest[1], full[1])
    data.rebind_train_steps(step=4, num_steps=4, step_period=1)
    assert len(data._loaders['train']) == 0

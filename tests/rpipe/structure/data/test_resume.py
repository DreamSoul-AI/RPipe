import pytest

from rpipe.structure.data.factory import Data

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_data,
              pytest.mark.cost(cost_class='c1'), pytest.mark.result_type('categorical', detail='summary')]


def _data(*, seed=7, workers=0, sequential=False, shared_generator=False):
    import torch
    from torch.utils.data import DataLoader, RandomSampler, SequentialSampler, TensorDataset

    dataset = TensorDataset(torch.arange(13), torch.zeros(13, dtype=torch.long))
    generator = torch.Generator().manual_seed(seed) if seed is not None else None
    sampler = SequentialSampler(dataset) if sequential else RandomSampler(dataset, generator=generator)
    loader = DataLoader(dataset, batch_size=3, sampler=sampler, num_workers=workers,
                        generator=generator if shared_generator else None)
    return Data(name='tiny', source='torch', loaders={'train': loader}, meta={'seed': seed})


@pytest.mark.parametrize('sequential,shared_generator', [(False, False), (False, True), (True, False)])
def test_restored_cursor_reads_suffix_without_replaying_samples(sequential, shared_generator):
    import torch

    data = _data(sequential=sequential, shared_generator=shared_generator)
    iterator = data.iter_batches('train')
    next(iterator)
    next(iterator)
    state = data.training_state()
    expected = [batch[0] for batch in iterator]
    restored = _data(sequential=sequential, shared_generator=shared_generator)
    assert restored.restore_training_state(state)
    rng = torch.get_rng_state()
    actual = [batch[0] for batch in restored.iter_batches('train')]
    assert torch.equal(rng, torch.get_rng_state())
    assert len(expected) == len(actual)
    for a, b in zip(actual, expected):
        assert torch.equal(a, b)


@pytest.mark.parametrize('change', ['seed', 'batch', 'identity', 'cursor', 'version'])
def test_restore_rejects_changed_or_invalid_data_state(change):
    data = _data()
    next(data.iter_batches('train'))
    state = data.training_state()
    if change == 'seed':
        state['seed'] = 8
    elif change == 'batch':
        state['spec']['batch_size'] = 1
    elif change == 'identity':
        state['identity']['source'] = 'other'
    elif change == 'cursor':
        state['consumed'] = -1
    else:
        state['version'] = 2
    with pytest.raises(ValueError, match='checkpoint'):
        _data().restore_training_state(state)


@pytest.mark.parametrize('seed,workers', [(None, 0), (7, 1)])
def test_unsupported_loader_does_not_claim_checkpoint_cursor(seed, workers):
    data = _data(seed=seed, workers=workers)
    assert data.training_state() is None


def test_epoch_boundary_restores_next_shuffle():
    import torch

    data = _data(shared_generator=True)
    list(data.iter_batches('train'))
    state = data.training_state()
    assert state['exhausted']
    expected = [batch[0] for batch in data.iter_batches('train')]
    restored = _data(shared_generator=True)
    assert not restored.restore_training_state(state)
    actual = [batch[0] for batch in restored.iter_batches('train')]
    for a, b in zip(actual, expected):
        assert torch.equal(a, b)

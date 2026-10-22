import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p2,
    pytest.mark.structure_layer,
    pytest.mark.module_system,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]

from rpipe.structure.contracts import SystemConfig
from rpipe.structure.system.runtime import apply_runtime, make_generator


@pytest.fixture
def preserve_tf32_flags():
    import torch
    before = (torch.backends.cudnn.allow_tf32, torch.backends.cuda.matmul.allow_tf32)
    yield
    torch.backends.cudnn.allow_tf32, torch.backends.cuda.matmul.allow_tf32 = before


@pytest.mark.parametrize('enabled', [False, True])
def test_runtime_applies_and_records_explicit_tf32(enabled, preserve_tf32_flags):
    import torch
    config = SystemConfig.from_mapping({'cudnn_allow_tf32': enabled, 'matmul_allow_tf32': enabled})
    applied = apply_runtime(None, config)
    assert torch.backends.cudnn.allow_tf32 is enabled
    assert torch.backends.cuda.matmul.allow_tf32 is enabled
    assert applied['cudnn_allow_tf32'] is enabled
    assert applied['matmul_allow_tf32'] is enabled


def test_omitted_tf32_preserves_existing_backend_values(preserve_tf32_flags):
    import torch
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cuda.matmul.allow_tf32 = True
    applied = apply_runtime(None)
    assert applied['cudnn_allow_tf32'] is False
    assert applied['matmul_allow_tf32'] is True


@pytest.mark.parametrize('name', ['cudnn_allow_tf32', 'matmul_allow_tf32'])
@pytest.mark.parametrize('value', ['false', 0, 1])
def test_invalid_precision_type_does_not_change_runtime(name, value, preserve_tf32_flags):
    import torch
    before = (torch.backends.cudnn.allow_tf32, torch.backends.cuda.matmul.allow_tf32)
    with pytest.raises(ValueError, match=f'system.{name} must be a boolean'):
        apply_runtime(None, SystemConfig.from_mapping({name: value}))
    assert (torch.backends.cudnn.allow_tf32, torch.backends.cuda.matmul.allow_tf32) == before


def test_apply_runtime_defaults_match_main_benchmark():
    applied = apply_runtime(0, SystemConfig())
    assert applied['seed'] == 0
    assert applied['deterministic'] is False
    assert applied['cudnn_deterministic'] is False
    assert applied['cudnn_benchmark'] is True


def test_apply_runtime_deterministic_turns_benchmark_off():
    cfg = SystemConfig.from_mapping({'deterministic': True})
    applied = apply_runtime(1, cfg)
    assert applied['deterministic'] is True
    assert applied['cudnn_deterministic'] is True
    assert applied['cudnn_benchmark'] is False


def test_make_generator_same_seed_same_draw():
    import torch

    a = make_generator(7)
    b = make_generator(7)
    c = make_generator(8)
    assert a is not None and b is not None and c is not None
    assert torch.rand(4, generator=a).tolist() == torch.rand(4, generator=b).tolist()
    assert torch.rand(4, generator=c).tolist() != torch.rand(4, generator=make_generator(7)).tolist()


def test_dataloader_shuffle_bound_to_seed():
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    data = torch.arange(32)
    dataset = TensorDataset(data)

    def first_batch(seed: int) -> list[int]:
        loader = DataLoader(
            dataset,
            batch_size=8,
            shuffle=True,
            generator=make_generator(seed),
        )
        batch, = next(iter(loader))
        return batch.tolist()

    assert first_batch(3) == first_batch(3)
    assert first_batch(3) != first_batch(4)

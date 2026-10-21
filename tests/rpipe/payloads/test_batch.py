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

def test_prepare_tensors_keeps_spatial_for_cnn():
    import torch

    from rpipe.payloads.batch import prepare_tensors
    from rpipe.payloads.model_architectures import CNN

    module = CNN((3, 32, 32), [64, 128, 256, 512], 10)
    images = torch.randn(2, 3, 32, 32)
    targets = torch.zeros(2, dtype=torch.long)
    got, _ = prepare_tensors((images, targets), module, torch.device('cpu'))
    assert tuple(got.shape) == (2, 3, 32, 32)


def test_prepare_tensors_flattens_nn_linear():
    import torch

    from rpipe.payloads.batch import prepare_tensors

    module = torch.nn.Linear(3072, 10)
    images = torch.randn(2, 3, 32, 32)
    targets = torch.zeros(2, dtype=torch.long)
    got, _ = prepare_tensors((images, targets), module, torch.device('cpu'))
    assert tuple(got.shape) == (2, 3072)

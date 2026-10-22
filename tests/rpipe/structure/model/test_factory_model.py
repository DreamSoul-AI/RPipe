import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p2,
    pytest.mark.structure_layer,
    pytest.mark.module_model,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]

from pathlib import Path

from rpipe.structure.model.factory import ModelFactory as model_api
from rpipe.structure.contracts import ModelConfig
from rpipe.structure.model.factory import ModelRegistry


def test_registry_lists_main_custom_torch_models():
    names = {name for name, source in ModelRegistry.list() if source == 'custom_torch'}
    assert {'linear', 'mlp', 'cnn', 'resnet18', 'resnet10', 'resnet', 'wresnet28x2', 'wresnet28x8', 'wresnet'} <= names


@pytest.mark.parametrize('mapping', [
    {'name': 'Typo'},
    {'name': 'linear', 'source': 'not-installed-source'},
    {'name': 'linear', 'source': 'stub'},
    {'name': 'linear', 'source': ''},
    {},
])
def test_unregistered_model_is_rejected(tmp_path, mapping):
    with pytest.raises(ValueError, match='unknown model name/source'):
        model_api.build(ModelConfig.from_mapping(mapping), tmp_path)


def test_linear_uses_data_meta_shape(tmp_path: Path):
    import torch

    model = model_api.build(
        ModelConfig.from_mapping({'name': 'linear'}),
        tmp_path,
        data_meta={'data_size': [3, 32, 32], 'target_size': 10},
    )
    x = torch.randn(2, 3, 32, 32)
    out = model.module(x)
    assert tuple(out.shape) == (2, 10)
    assert model.module.output_proj.bias.eq(0).all()


def test_input_norm_sits_in_front_of_the_network(tmp_path: Path):
    import torch

    model = model_api.build(
        ModelConfig.from_mapping({'name': 'linear'}),
        tmp_path,
        data_meta={
            'data_size': [1, 2, 2],
            'target_size': 2,
            'mean': (0.5,),
            'std': (0.5,),
        },
    )
    x = torch.zeros(1, 1, 2, 2)
    out = model.module(x)
    bare = model.module.net(x)
    assert tuple(out.shape) == (1, 2)
    assert not torch.allclose(out, bare)


def test_kornia_crop_and_flip_run_only_while_training(tmp_path: Path):
    import torch

    model = model_api.build(
        ModelConfig.from_mapping({'name': 'linear'}),
        tmp_path,
        data_meta={
            'data_size': [3, 8, 8],
            'target_size': 10,
            'mean': (0.0, 0.0, 0.0),
            'std': (1.0, 1.0, 1.0),
            'augment': True,
            'train_aug': 'cifar',
        },
    )
    assert model.module.train_aug is not None
    x = torch.zeros(2, 3, 8, 8)
    x[:, :, :, 4:] = 1
    model.module.eval()
    first = model.module(x)
    second = model.module(x)
    assert torch.allclose(first, second)
    model.module.train()
    trained = model.module(x)
    assert tuple(trained.shape) == (2, 10)


def test_mlp_cnn_resnet_forward_cifar_shape(tmp_path: Path):
    import torch

    meta = {'data_size': [3, 32, 32], 'target_size': 10}
    x = torch.randn(2, 3, 32, 32)
    for name in ('mlp', 'cnn', 'resnet18', 'resnet10', 'wresnet28x2', 'wresnet28x8'):
        model = model_api.build(ModelConfig.from_mapping({'name': name}), tmp_path, data_meta=meta)
        out = model.module(x)
        assert tuple(out.shape) == (2, 10), name


@pytest.mark.parametrize('config,meta,expected', [
    ({}, None, ((1, 28, 28), 10)),
    ({'data_size': [2, 3], 'target_size': 5}, {'data_size': [4], 'target_size': 7}, ((2, 3), 5)),
    ({'out_features': 0}, {'target_size': 7}, ((1, 28, 28), 7)),
    ({'data_size': []}, None, ((), 10)),
    ({'in_features': 0}, None, ((0,), 10)),
    ({'in_features': '6'}, None, ((6,), 10)),
    ({'in_features': 9}, {'data_size': [4]}, ((4,), 10)),
    ({'in_features': 9}, {'data_size': []}, ((), 10)),
])
def test_factory_shape_resolution_preserves_precedence_and_empty_dimensions(config, meta, expected):
    from copy import deepcopy
    from rpipe.structure.model.factory import resolve_shape
    before = deepcopy((config, meta))
    assert resolve_shape(config, meta) == expected
    assert (config, meta) == before


@pytest.mark.parametrize('config', [{'target_size': 'bad'}, {'data_size': ['bad']}])
def test_factory_shape_resolution_keeps_invalid_integer_error(config):
    from rpipe.structure.model.factory import resolve_shape
    with pytest.raises(ValueError):
        resolve_shape(config, None)

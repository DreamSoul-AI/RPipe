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

from rpipe.structure.model.factory import build_model as model_api
from rpipe.structure.control import ModelConfig
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
def test_unregistered_model_is_rejected(tmp_path, mapping, model_system):
    with pytest.raises(ValueError, match='unknown model name/source'):
        model_api(ModelConfig.from_mapping(mapping), tmp_path, system=model_system, data_meta=None)


def test_linear_uses_data_meta_shape(tmp_path: Path, model_system):
    import torch

    model = model_api(
        ModelConfig.from_mapping({'name': 'linear'}),
        tmp_path,
        data_meta={'data_size': [3, 32, 32], 'target_size': 10}, system=model_system,
    )
    x = torch.randn(2, 3, 32, 32)
    out = model.module(x)
    assert tuple(out.shape) == (2, 10)
    assert model.module.output_proj.bias.eq(0).all()


def test_input_norm_sits_in_front_of_the_network(tmp_path: Path, model_system):
    import torch

    model = model_api(
        ModelConfig.from_mapping({'name': 'linear'}),
        tmp_path,
        data_meta={
            'data_size': [1, 2, 2],
            'target_size': 2,
            'mean': (0.5,),
            'std': (0.5,),
        }, system=model_system,
    )
    x = torch.zeros(1, 1, 2, 2)
    out = model.module(x)
    bare = model.module.net(x)
    assert tuple(out.shape) == (1, 2)
    assert not torch.allclose(out, bare)


def test_kornia_crop_and_flip_run_only_while_training(tmp_path: Path, model_system):
    import torch

    model = model_api(
        ModelConfig.from_mapping({'name': 'linear'}),
        tmp_path,
        data_meta={
            'data_size': [3, 8, 8],
            'target_size': 10,
            'mean': (0.0, 0.0, 0.0),
            'std': (1.0, 1.0, 1.0),
            'augment': True,
            'train_aug': 'cifar',
        }, system=model_system,
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


def test_mlp_cnn_resnet_forward_cifar_shape(tmp_path: Path, model_system):
    import torch

    meta = {'data_size': [3, 32, 32], 'target_size': 10}
    x = torch.randn(2, 3, 32, 32)
    for name in ('mlp', 'cnn', 'resnet18', 'resnet10', 'wresnet28x2', 'wresnet28x8'):
        model = model_api(ModelConfig.from_mapping({'name': name}), tmp_path, data_meta=meta, system=model_system)
        out = model.module(x)
        assert tuple(out.shape) == (2, 10), name


@pytest.mark.parametrize('config,meta,expected', [
    ({}, None, ((1, 28, 28), 10)),
    ({'data_size': [2, 3], 'target_size': 5}, {'data_size': [4], 'target_size': 7}, ((2, 3), 5)),
    ({'out_features': 0}, {'target_size': 7}, ((1, 28, 28), 7)),
    ({'data_size': []}, None, ((), 10)),
    ({'in_features': 0}, None, ((0,), 10)),
    ({'in_features': '6'}, None, ((6,), 10)),
    ({'in_features': 4}, {'data_size': [4]}, ((4,), 10)),
    ({'in_features': 1}, {'data_size': []}, ((), 10)),
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


@pytest.mark.parametrize('config,meta', [
    ({'in_features': 3}, {'data_size': [2]}),
    ({'in_features': 3, 'data_size': [2]}, None),
])
def test_explicit_linear_width_cannot_disagree_with_the_selected_shape(config, meta):
    from rpipe.structure.model.factory import resolve_shape
    with pytest.raises(ValueError, match='in_features'):
        resolve_shape(config, meta)


def test_explicit_linear_width_accepts_matching_flattened_image_shape(tmp_path, model_system):
    import torch
    model = model_api(ModelConfig.from_mapping({'name': 'linear', 'config': {'in_features': 784}}),
                      tmp_path, data_meta={'data_size': [1, 28, 28], 'target_size': 2},
                      system=model_system)
    assert model.meta['in_features'] == 784
    assert tuple(model.module(torch.zeros(2, 1, 28, 28)).shape) == (2, 2)


def test_model_build_places_the_normalized_module_once_through_injected_system(tmp_path):
    from rpipe.structure.model.input_norm import InputNorm
    placed, sentinel = [], object()
    class Placement:
        def place_module(self, module):
            assert isinstance(module, InputNorm)
            placed.append(module)
            return sentinel
    meta = {'data_size': [1, 2, 2], 'target_size': 2, 'mean': [0.5], 'std': [0.5]}
    model = model_api(ModelConfig.from_mapping({'name': 'linear'}), tmp_path,
                      data_meta=meta, system=Placement())
    assert len(placed) == 1
    assert model.module is sentinel


def test_input_pipeline_keeps_flip_crop_normalize_network_order(monkeypatch):
    import sys
    from types import SimpleNamespace
    import torch
    from rpipe.structure.model.input_norm import InputNorm
    events = []
    class Operation(torch.nn.Module):
        def __init__(self, name):
            super().__init__()
            self.name = name
        def forward(self, value):
            events.append(self.name)
            return value
    monkeypatch.setitem(sys.modules, 'kornia.augmentation', SimpleNamespace(
        Normalize=lambda **kwargs: Operation('normalize'),
        RandomCrop=lambda *args, **kwargs: Operation('crop'),
        RandomHorizontalFlip=lambda **kwargs: Operation('flip')))
    module = InputNorm(Operation('network'), (0.5,), (0.5,), augment='cifar', spatial=2)
    module(torch.zeros(1, 1, 2, 2))
    assert events == ['flip', 'crop', 'normalize', 'network']
    events.clear()
    module.eval()
    module(torch.zeros(1, 1, 2, 2))
    assert events == ['normalize', 'network']

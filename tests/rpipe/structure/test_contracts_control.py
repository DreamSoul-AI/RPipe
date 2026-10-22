import pytest

from rpipe.structure.contracts import (
    ExperimentConfig,
    control_from_config,
    control_to_config,
    run_config_from_merge,
    validate_run_config,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.structure_layer,
    pytest.mark.module_control,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]


def test_control_from_config_hashes_id_and_layers():
    cfg = {
        'seed': 0,
        'experiment': 'mnist_linear',
        'data': {'name': 'MNIST'},
        'model': {'name': 'linear'},
        'algorithm': {'mode': 'train'},
        'system': {'device': 'cpu'},
    }
    control = control_from_config(cfg)
    assert control.id
    assert len(control.id) == 16
    assert control.seed == 0
    assert control.data['name'] == 'MNIST'
    assert control.to_dict()['model']['name'] == 'linear'
    assert control.to_dict()['id'] == control.id


def test_same_content_same_id_different_content_different_id():
    a = control_from_config({'seed': 0, 'data': {'name': 'MNIST'}})
    b = control_from_config({'seed': 0, 'data': {'name': 'MNIST'}})
    c = control_from_config({'seed': 1, 'data': {'name': 'MNIST'}})
    assert a.id == b.id
    assert a.id != c.id


def test_run_config_from_merge_patches_experiment_base():
    base = ExperimentConfig.from_mapping(
        {
            'experiment': 'mnist_linear',
            'data': {'name': 'MNIST', 'source': 'torch'},
            'algorithm': {'mode': 'train'},
        }
    )
    run = run_config_from_merge(base, {'seed': 0, 'data': {'path': '/data/mnist'}})
    assert run.seed == 0
    assert run.experiment == 'mnist_linear'
    assert run.data.name == 'MNIST'
    assert run.data.source == 'torch'
    assert run.data.path == '/data/mnist'
    assert run.id
    validate_run_config(run)


def test_control_to_config_roundtrip():
    control = control_from_config(
        {
            'seed': 2,
            'data': {'name': 'CIFAR'},
            'algorithm': {'mode': 'eval'},
        }
    )
    mapping = control_to_config(control)
    again = control_from_config(mapping)
    assert again.id == control.id
    assert again.seed == 2
    assert again.algorithm['mode'] == 'eval'


def test_legacy_slug_ignored_for_id():
    a = control_from_config({'slug': 'seed_0', 'seed': 0, 'data': {'name': 'X'}})
    b = control_from_config({'seed': 0, 'data': {'name': 'X'}})
    assert a.id == b.id


def test_description_ignored_tags_affect_run_id():
    a = control_from_config(
        {'seed': 0, 'data': {'name': 'X'}, 'description': 'one', 'tags': ['baseline']}
    )
    b = control_from_config(
        {'seed': 0, 'data': {'name': 'X'}, 'description': 'two', 'tags': ['baseline']}
    )
    c = control_from_config(
        {'seed': 0, 'data': {'name': 'X'}, 'description': 'one', 'tags': ['smoke']}
    )
    assert a.id == b.id  # description excluded from hash
    assert a.id != c.id  # tags included in hash
    assert a.to_dict()['tags'] == ['baseline']
    assert c.to_dict()['tags'] == ['smoke']


def test_omitted_version_preserves_existing_run_id():
    control = control_from_config({'seed': 0, 'data': {'name': 'MNIST'}})
    # Existing content identity, before adding any version declaration.
    assert control.id == '96a1993415ec5eae'
    assert 'version' not in control_to_config(control)


@pytest.mark.parametrize('version', [7, 'repeat-1', '2026-10-02T10:00:00+08:00'])
def test_version_same_value_reuses_id_new_value_changes_id(version):
    config = {'seed': 0, 'data': {'name': 'MNIST'}, 'version': version}
    first = control_from_config(config)
    same = control_from_config(dict(config))
    different = control_from_config({**config, 'version': 'another-repeat'})
    assert first.id == same.id
    assert first.id != different.id
    assert control_to_config(first)['version'] == version


@pytest.mark.parametrize(
    ('patch', 'expected_version'),
    [({}, 'base'), ({'version': 'repeat'}, 'repeat')],
    ids=['inherit-base', 'override-base'],
)
def test_version_merge_and_roundtrip_preserve_value_and_id(patch, expected_version):
    base = ExperimentConfig.from_mapping({'version': 'base', 'data': {'name': 'MNIST'}})
    run = run_config_from_merge(base, {'seed': 0, **patch})
    mapping = run.to_mapping()
    # Merge recomputes the ID instead of trusting a serialized id field.
    again = run_config_from_merge(mapping)
    assert base.to_mapping()['version'] == 'base'
    assert mapping['version'] == expected_version
    assert again.to_mapping() == mapping


@pytest.mark.parametrize('kind,identifier', [('experiment', None), ('run', ''), ('run', 'chosen')])
def test_root_mapping_preserves_layer_calls_extras_identity_and_id_position(kind, identifier):
    from rpipe.structure.contracts import RunConfig
    calls = []
    class Layer:
        def __init__(self, label):
            self.label = label
        def to_mapping(self):
            calls.append(self.label)
            return {'label': self.label}
    token = object()
    tags = ('x',)
    options = dict(seed=0, experiment='', description='', tags=tags,
                   extras={'seed': None, 'data': token, 'id': 'extra', 'tail': token},
                   **{name: Layer(name) for name in ('data', 'model', 'algorithm', 'system')})
    value = ExperimentConfig(**options) if kind == 'experiment' else RunConfig(id=identifier, **options)
    result = value.to_mapping()
    assert calls == ['data', 'model', 'algorithm', 'system']
    keys = ['seed', 'experiment', 'description', 'tags', 'data', 'model', 'algorithm', 'system']
    assert list(result) == (['id', *keys, 'tail'] if identifier else [*keys, 'id', 'tail'])
    assert result['id'] == 'extra' and result['seed'] is None
    assert result['data'] is token and result['tail'] is token
    assert result['tags'] == ['x'] and result['tags'] is not tags


@pytest.mark.parametrize('kind', ['experiment', 'run'])
def test_root_mapping_layer_failure_preserves_original_exception_and_call_order(kind):
    from rpipe.structure.contracts import RunConfig
    failure, calls = RuntimeError('model mapping refused'), []
    class Layer:
        def __init__(self, label):
            self.label = label
        def to_mapping(self):
            calls.append(self.label)
            if self.label == 'model':
                raise failure
            return {}
    cls = ExperimentConfig if kind == 'experiment' else RunConfig
    value = cls(**{name: Layer(name) for name in ('data', 'model', 'algorithm', 'system')})
    with pytest.raises(RuntimeError) as caught:
        value.to_mapping()
    assert caught.value is failure and calls == ['data', 'model']


@pytest.mark.parametrize('kind', ['experiment', 'run'])
def test_root_mapping_does_not_expand_subclass_default_fields(kind):
    from dataclasses import dataclass
    from rpipe.structure.contracts import RunConfig
    base = ExperimentConfig if kind == 'experiment' else RunConfig
    @dataclass
    class Extended(base):
        future: str = 'default'
    value = Extended(seed=0)
    assert value.future == 'default' and 'future' not in value.to_mapping()

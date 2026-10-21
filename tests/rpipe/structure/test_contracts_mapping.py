import pytest

from rpipe.structure.contracts import AlgorithmConfig
from rpipe.structure.contracts import SystemConfig

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_control,
              pytest.mark.cost(cost_class='c1'), pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('config_type,key', [(SystemConfig, 'device'), (AlgorithmConfig, 'num_steps')])
@pytest.mark.parametrize('nested', [None, {'value': 'nested'}, {'value': None}])
def test_raw_setting_lookup_matches_runtime_config(config_type, key, nested):
    from rpipe.structure.contracts import get_setting

    mapping = {key: 'outer'}
    if nested is not None:
        mapping['config'] = {key: nested['value']}
    assert get_setting(mapping, key) == config_type.from_mapping(mapping).setting(key)


from rpipe.structure.contracts import DataConfig
from rpipe.structure.contracts import ModelConfig


@pytest.mark.parametrize('config_type', [DataConfig, ModelConfig, AlgorithmConfig, SystemConfig])
@pytest.mark.parametrize('kind', ['none', 'dict', 'readonly'])
def test_layer_mapping_preserves_input_and_shallow_copy_contract(config_type, kind):
    from types import MappingProxyType

    if kind == 'none':
        assert config_type.from_mapping(None).to_mapping() == {}
        return
    body = {'source': 'stub', 'config': {'x': 1}, 'custom': {'leaf': 2}}
    mapping = MappingProxyType(body) if kind == 'readonly' else body
    config = config_type.from_mapping(mapping)
    assert body == {'source': 'stub', 'config': {'x': 1}, 'custom': {'leaf': 2}}
    assert config.config is not body['config']
    assert config.extras['custom'] is body['custom']
    config.config['x'] = 3
    assert body['config']['x'] == 1
    emitted = config.to_mapping()
    assert emitted['config'] == {'x': 3} and emitted['config'] is not config.config
    assert emitted['custom'] is body['custom']


@pytest.mark.parametrize('config_type', [DataConfig, ModelConfig, AlgorithmConfig, SystemConfig])
def test_layer_mapping_preserves_extras_override_and_identity(config_type):
    leaf, shadow = object(), {'override': True}
    config = config_type.from_mapping({'source': 'stub', 'config': {'x': 1}, 'custom': leaf})
    config.extras.update(source='override', config=shadow)
    result = config.to_mapping()
    assert result['source'] == 'override' and result['config'] is shadow
    assert result['custom'] is leaf
    assert config.source == 'stub' and config.config == {'x': 1}
    assert list(result) == ['source', 'config', 'custom']


@pytest.mark.parametrize('config_type,order', [
    (DataConfig, ['name', 'source', 'path', 'config', 'mode', 'custom']),
    (ModelConfig, ['name', 'source', 'path', 'config', 'mode', 'custom']),
    (AlgorithmConfig, ['mode', 'source', 'path', 'config', 'name', 'custom']),
    (SystemConfig, ['source', 'path', 'config', 'name', 'mode', 'custom']),
])
def test_layer_mapping_keeps_existing_field_order(config_type, order):
    body = dict(name='n', mode='train', source='stub', path='p', config={'x': 1}, custom=2)
    assert list(config_type.from_mapping(body).to_mapping()) == order


@pytest.mark.parametrize('value', [None, False, 2, 'replacement', [1, 2]])
def test_deep_merge_leaf_replacement_keeps_patch_identity(value):
    from rpipe.structure.contracts import deep_merge
    base, patch = {'x': {'old': 1}}, {'x': value}
    assert deep_merge(base, patch)['x'] is value
    assert base == {'x': {'old': 1}}


@pytest.mark.parametrize('base,patch', [
    (None, {'x': 1}), ([1], {'x': 1}), ({'x': 1}, [2]), ({'x': 1}, None), ([1], []),
])
def test_deep_merge_nonmapping_replaces_without_copy(base, patch):
    from rpipe.structure.contracts import deep_merge
    assert deep_merge(base, patch) is patch


def test_deep_merge_copies_changed_branches_and_retains_other_references():
    from rpipe.structure.contracts import deep_merge
    kept, added = {'leaf': 1}, [2]
    base = {'nested': {'a': 1}, 'kept': kept}
    patch = {'nested': {'b': 2}, 'new': added}
    result = deep_merge(base, patch)
    assert result == {'nested': {'a': 1, 'b': 2}, 'kept': kept, 'new': added}
    assert result['nested'] is not base['nested'] and result['nested'] is not patch['nested']
    assert result['kept'] is kept and result['new'] is added
    assert base == {'nested': {'a': 1}, 'kept': kept} and patch['nested'] == {'b': 2}
    assert list(result) == ['nested', 'kept', 'new']


@pytest.mark.parametrize('config_type', [DataConfig, ModelConfig, AlgorithmConfig, SystemConfig])
def test_layer_mapping_retains_subclass_default_without_expanding_fields(config_type):
    from dataclasses import dataclass

    @dataclass
    class Child(config_type):
        extra_field: str = 'kept'

    child = Child.from_mapping({'extra_field': 'ignored', 'custom': 1})
    assert child.extra_field == 'kept'
    assert child.extras == {'custom': 1}
    assert child.to_mapping() == {'custom': 1}


@pytest.mark.parametrize('config_type,names', [
    (DataConfig, ['name', 'source', 'path', 'config', 'extras']),
    (ModelConfig, ['name', 'source', 'path', 'config', 'extras']),
    (AlgorithmConfig, ['mode', 'source', 'path', 'config', 'extras']),
    (SystemConfig, ['source', 'path', 'config', 'extras']),
])
def test_layer_mapping_keeps_constructor_keyword_order(config_type, names):
    seen = []

    class Probe(config_type):
        def __init__(self, **kwargs):
            seen.append(list(kwargs))
            super().__init__(**kwargs)

    Probe.from_mapping(None)
    assert seen == [names]


def test_data_subclass_origin_is_still_rejected_before_construction():
    from dataclasses import dataclass

    @dataclass
    class OriginData(DataConfig):
        origin: str | None = None

    with pytest.raises(ValueError, match='origin is set on the Study'):
        OriginData.from_mapping({'origin': 'domestic'})


@pytest.mark.parametrize('match', [None, 'dict', 'list'])
def test_emit_preserves_equality_omission_order_and_value_identity(match):
    from rpipe.structure.contracts import emit
    calls = []
    class Probe:
        def __eq__(self, other):
            calls.append(type(other).__name__)
            return type(other).__name__ == match
        def __ne__(self, other):
            pytest.fail('omission must retain equality semantics')
    probe, tail = Probe(), object()
    result = emit({'probe': probe, 'none': None, 'empty': [], 'zero': 0, 'false': False}, {'tail': tail})
    assert calls == (['dict'] if match == 'dict' else ['dict', 'list'])
    assert list(result) == (['probe', 'zero', 'false', 'tail'] if match is None else ['zero', 'false', 'tail'])
    if match is None:
        assert result['probe'] is probe
    assert result['tail'] is tail and result['zero'] == 0 and result['false'] is False

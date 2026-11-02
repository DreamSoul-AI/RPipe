"""Layer declarations and deep overrides preserve finite JSON conditions."""

import copy
import json
from types import MappingProxyType

import pytest

from rpipe.structure.control import (
    AlgorithmConfig, ContractError, DataConfig, ModelConfig, SystemConfig,
    deep_merge, get_setting,
)


pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_control,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]

LAYERS = [DataConfig, ModelConfig, AlgorithmConfig, SystemConfig]


@pytest.mark.parametrize('config_type,key', [(SystemConfig, 'device'), (AlgorithmConfig, 'num_steps')])
@pytest.mark.parametrize('nested', [None, {'value': 'nested'}, {'value': None}])
def test_nested_settings_take_precedence_even_when_explicitly_null(config_type, key, nested):
    mapping = {key: 'outer'}
    expected = 'outer'
    if nested is not None:
        mapping['config'] = {key: nested['value']}
        expected = nested['value']
    assert get_setting(mapping, key) == expected
    assert config_type.from_mapping(mapping).setting(key) == expected


@pytest.mark.parametrize('config_type', LAYERS)
@pytest.mark.parametrize('mapping', [None, {}], ids=['omitted', 'empty'])
def test_empty_layer_conditions_have_empty_serializable_projection(config_type, mapping):
    assert config_type.from_mapping(mapping).to_mapping() == {}


@pytest.mark.parametrize('config_type', LAYERS)
@pytest.mark.parametrize('readonly', [False, True], ids=['dict', 'readonly-mapping'])
def test_layer_conditions_roundtrip_without_mutating_the_input(config_type, readonly):
    body = {'source': 'stub', 'config': {'x': 1, 'shape': [1, 4, 4]},
            'custom': {'enabled': False, 'limit': 0}}
    original = copy.deepcopy(body)
    mapping = MappingProxyType(body) if readonly else body
    config = config_type.from_mapping(mapping)
    projected = config.to_mapping()
    assert body == original and projected == original
    restored = config_type.from_mapping(json.loads(json.dumps(projected)))
    assert restored.to_mapping() == original


@pytest.mark.parametrize('config_type', LAYERS)
@pytest.mark.parametrize('value', [None, False, 0, '', [], {}])
def test_explicit_content_values_are_not_replaced_by_defaults(config_type, value):
    mapping = {'config': {'choice': value}}
    projected = config_type.from_mapping(mapping).to_mapping()
    assert projected['config']['choice'] == value


@pytest.mark.parametrize('config_type', LAYERS)
@pytest.mark.parametrize('value', [object(), {1, 2}, float('nan'), float('inf')],
                         ids=['runtime-object', 'set', 'nan', 'infinity'])
def test_layer_conditions_reject_runtime_and_nonfinite_values(config_type, value):
    with pytest.raises(ContractError):
        config_type.from_mapping({'config': {'invalid': value}})


@pytest.mark.parametrize('value', [None, False, 0, 'replacement', [1, 2]])
def test_deep_override_replaces_explicit_leaf_values_without_mutating_inputs(value):
    base = {'nested': {'old': 1}, 'kept': {'value': 2}}
    patch = {'nested': value}
    before_base, before_patch = copy.deepcopy(base), copy.deepcopy(patch)
    merged = deep_merge(base, patch)
    assert merged == {'nested': value, 'kept': {'value': 2}}
    assert base == before_base and patch == before_patch


def test_deep_merge_retains_unmodified_conditions_and_replaces_lists():
    base = {'algorithm': {'config': {'lr': .1, 'shape': [1, 2]}},
            'data': {'name': 'Toy'}}
    patch = {'algorithm': {'config': {'shape': [3], 'enabled': False}}}
    before_base, before_patch = copy.deepcopy(base), copy.deepcopy(patch)
    assert deep_merge(base, patch) == {
        'algorithm': {'config': {'lr': .1, 'shape': [3], 'enabled': False}},
        'data': {'name': 'Toy'}}
    assert base == before_base and patch == before_patch


def test_data_source_policy_is_declared_at_study_scope():
    with pytest.raises(ContractError, match='origin is set on the Study'):
        DataConfig.from_mapping({'origin': 'domestic'})

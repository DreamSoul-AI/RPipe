"""Model construction uses positive dimensions and truthful post-transform metadata."""
from copy import deepcopy
import pytest
from rpipe.structure.model.factory import resolve_shape

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_model,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('config,meta,expected', [
    ({}, None, ((1, 28, 28), 10)),
    ({}, {'data_size': [3, 32, 32], 'target_size': 100}, ((3, 32, 32), 100)),
    ({'data_size': [2, 3], 'target_size': 5}, {'data_size': [2, 3], 'target_size': 5}, ((2, 3), 5)),
    ({'in_features': 6}, {'data_size': [2, 3], 'target_size': 2}, ((2, 3), 2)),
    ({'data_size': [6], 'target_size': 2}, {'data_size': [6], 'target_size': 2}, ((6,), 2)),
    ({'out_features': 7}, None, ((1, 28, 28), 7)),
])
def test_shape_accepts_consistent_or_explicitly_transformed_inputs(config, meta, expected):
    before = deepcopy((config, meta))
    assert resolve_shape(config, meta) == expected
    assert (config, meta) == before


@pytest.mark.parametrize('config,meta', [
    ({'data_size': [1, 28, 28]}, {'data_size': [3, 32, 32]}),
    ({'target_size': 10}, {'target_size': 100}),
    ({'out_features': 10}, {'target_size': 100}),
    ({'target_size': 5, 'out_features': 7}, None),
    ({'data_size': [6]}, {'data_size': [2, 3]}),
    ({'in_features': 4}, {'data_size': [2, 3]}),
])
def test_shape_rejects_conflicting_facts_without_mutation(config, meta):
    before = deepcopy((config, meta))
    with pytest.raises(ValueError):
        resolve_shape(config, meta)
    assert (config, meta) == before


@pytest.mark.parametrize('key,value', [
    ('target_size', 0), ('target_size', -1), ('target_size', True),
    ('target_size', 2.5), ('target_size', '6'), ('out_features', 0), ('out_features', 'bad'),
    ('in_features', '6'), ('in_features', 0), ('in_features', -1), ('in_features', 2.5),
    ('data_size', []), ('data_size', [0]), ('data_size', [-1]),
    ('data_size', [True]), ('data_size', ['6']), ('data_size', [2.5]), ('data_size', ['bad']),
])
def test_shape_never_replaces_invalid_explicit_values_with_defaults(key, value):
    with pytest.raises(ValueError):
        resolve_shape({key: value}, {'data_size': [2], 'target_size': 3})


@pytest.mark.parametrize('meta', [
    {'data_size': []}, {'data_size': [0]}, {'data_size': [-1]},
    {'data_size': [True]}, {'target_size': 0}, {'target_size': False},
])
def test_shape_rejects_invalid_data_metadata_even_when_config_is_explicit(meta):
    with pytest.raises(ValueError):
        resolve_shape({'data_size': [2], 'target_size': 3}, meta)


@pytest.mark.parametrize('config,meta', [
    ({'target_size': 10}, {'target_size': 100}),
    ({'data_size': [6]}, {'data_size': [2, 3]}),
    ({'data_size': [0]}, {'data_size': [2]}),
])
def test_public_build_rejects_before_builder_origin_or_cache(tmp_path, monkeypatch, config, meta):
    from rpipe.structure.model import factory
    from rpipe.structure.control import ModelConfig
    calls = []
    monkeypatch.setitem(factory.ModelRegistry._items, ('synthetic', 'contract'),
                        lambda *args, **kwargs: calls.append('builder'))
    monkeypatch.setattr(factory, 'apply_model_origin', lambda origin: calls.append('origin'))
    output = tmp_path / 'must-not-exist'
    with pytest.raises(ValueError):
        factory.build_model(ModelConfig.from_mapping({'name': 'synthetic', 'source': 'contract', 'config': config}),
                            output, data_meta=meta, system=object(), origin='foreign')
    assert calls == []
    assert not output.exists()

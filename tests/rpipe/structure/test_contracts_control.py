"""Resolved conditions and persisted Run configuration retain scientific meaning."""

import copy
import json

import pytest

from rpipe.structure.control import (
    ContractError, RunConfig, compute_run_id, resolve_control, validate_control,
)


pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_control,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def _config():
    return {'seed': 0, 'experiment': 'toy-linear',
            'data': {'name': 'Toy', 'source': 'stub'},
            'model': {'name': 'linear'}, 'algorithm': {'mode': 'train'},
            'system': {'device': 'cpu'}}


def test_resolved_control_contains_four_layer_conditions_and_content_identity():
    config = _config()
    control = resolve_control(config)
    validate_control(control)
    assert control.seed == 0 and control.experiment == 'toy-linear'
    assert control.data == config['data']
    assert control.model == config['model']
    assert control.algorithm == config['algorithm']
    assert control.system == config['system']
    assert control.id == compute_run_id(control.to_dict())


def test_equal_conditions_share_identity_and_changed_scientific_condition_does_not():
    config = _config()
    first = resolve_control(config)
    same = resolve_control(copy.deepcopy(config))
    changed = resolve_control(config, {'model': {'name': 'mlp'}})
    assert first.id == same.id and changed.id != first.id


def test_resolved_conditions_survive_json_and_run_config_roundtrip():
    config = {**_config(), 'seed': 2, 'description': 'one repeat', 'version': 'trial-1'}
    control = resolve_control(config)
    saved = json.loads(json.dumps(control.to_dict(), allow_nan=False))
    run = RunConfig.from_mapping(saved)
    again = resolve_control(run.to_mapping())
    assert run.id == again.id == control.id
    assert again.to_dict() == saved


def test_nested_overrides_retain_unmodified_conditions_without_mutating_input():
    base = _config()
    base['data']['config'] = {'batch_size': 2, 'shape': [1, 4, 4]}
    base['algorithm']['config'] = {'lr': .1, 'optimizer': 'SGD'}
    factors = {'data.config.batch_size': 4, 'algorithm': {'config': {'lr': .2}}}
    before_base, before_factors = copy.deepcopy(base), copy.deepcopy(factors)
    control = resolve_control(base, factors, seed=7)
    assert control.data['config'] == {'batch_size': 4, 'shape': [1, 4, 4]}
    assert control.algorithm['config'] == {'lr': .2, 'optimizer': 'SGD'}
    assert control.seed == 7
    assert base == before_base and factors == before_factors


def test_description_is_human_metadata_but_tags_participate_in_identity():
    base = {**_config(), 'description': 'first note', 'tags': ['baseline']}
    first = resolve_control(base)
    described = resolve_control(base, {'description': 'second note'})
    tagged = resolve_control(base, {'tags': ['comparison']})
    assert first.id == described.id and first.id != tagged.id
    assert described.to_dict()['description'] == 'second note'


def test_omitted_version_stays_omitted_through_roundtrip():
    control = resolve_control(_config())
    restored = RunConfig.from_mapping(json.loads(json.dumps(control.to_dict())))
    assert 'version' not in restored.to_mapping()
    assert restored.id == control.id


@pytest.mark.parametrize('version', [7, 'repeat-1', '2026-10-29T10:00:00+08:00'])
def test_declared_version_survives_serialization_and_distinguishes_repeats(version):
    config = {**_config(), 'version': version}
    first = resolve_control(config)
    same = resolve_control(json.loads(json.dumps(config)))
    different = resolve_control(config, {'version': 'another-repeat'})
    assert first.id == same.id and different.id != first.id
    assert RunConfig.from_mapping(first.to_dict()).to_mapping()['version'] == version


@pytest.mark.parametrize('factors,version', [({}, 'base'), ({'version': 'repeat'}, 'repeat')])
def test_version_override_recomputes_identity_instead_of_trusting_previous_id(factors, version):
    base = resolve_control({**_config(), 'version': 'base'}).to_dict()
    before = copy.deepcopy(base)
    control = resolve_control(base, factors)
    assert base == before
    assert control.to_dict()['version'] == version
    assert control.id == compute_run_id(control.to_dict())
    if factors:
        assert control.id != base['id']


def test_persisted_id_cannot_disagree_with_effective_conditions():
    saved = resolve_control(_config()).to_dict()
    saved['data']['name'] = 'different-data'
    with pytest.raises(ContractError, match='id'):
        RunConfig.from_mapping(saved)


def test_removed_slug_field_is_rejected_instead_of_ignored():
    with pytest.raises(ContractError, match='slug'):
        resolve_control({**_config(), 'slug': 'old-run-name'})


def test_empty_optional_fields_and_zero_values_survive_configuration_roundtrip():
    config = {**_config(), 'experiment': '', 'description': '',
              'algorithm': {'mode': 'train', 'config': {'lr': 0, 'enabled': False}},
              'data': {'name': 'Toy', 'path': '', 'config': {}}}
    saved = resolve_control(config).to_dict()
    restored = RunConfig.from_mapping(json.loads(json.dumps(saved))).to_mapping()
    assert restored == saved
    assert restored['experiment'] == restored['description'] == restored['data']['path'] == ''
    assert restored['algorithm']['config'] == {'lr': 0, 'enabled': False}


@pytest.mark.parametrize('value', [object(), {1, 2}, float('nan'), float('inf')],
                         ids=['runtime-object', 'set', 'nan', 'infinity'])
def test_non_json_or_nonfinite_conditions_are_explicitly_rejected(value):
    config = _config()
    config['algorithm']['config'] = {'custom': value}
    with pytest.raises(ContractError):
        resolve_control(config)

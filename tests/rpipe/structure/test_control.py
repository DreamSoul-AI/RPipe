"""Effective experiment conditions are pure, validated, and independent of I/O."""

import copy
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_control,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def _base():
    return {'seed': 3, 'data': {'name': 'Toy', 'source': 'stub',
                               'config': {'batch_size': 2, 'train_size': 8}},
            'model': {'name': 'linear'},
            'algorithm': {'mode': 'train', 'config': {'lr': .1}},
            'system': {'device': 'cpu'}}


def test_resolve_control_applies_factors_and_seed_without_mutating_inputs():
    from rpipe.structure.control import resolve_control, validate_control

    base = _base()
    factors = {'data': {'config': {'batch_size': 4}},
               'algorithm': {'config': {'lr': .2}}}
    before_base, before_factors = copy.deepcopy(base), copy.deepcopy(factors)
    control = resolve_control(base, factors, seed=7)
    validate_control(control)
    assert control.seed == 7
    assert control.data['config'] == {'batch_size': 4, 'train_size': 8}
    assert control.algorithm['config']['lr'] == .2
    assert base == before_base and factors == before_factors
    assert resolve_control(base, factors, seed=7).id == control.id
    assert resolve_control(base, factors, seed=8).id != control.id


def test_control_resolution_performs_no_filesystem_operation(monkeypatch):
    from rpipe.structure.control import resolve_control, validate_control

    def forbidden(*args, **kwargs):
        raise AssertionError('pure control attempted filesystem access')

    with monkeypatch.context() as guard:
        for name in ('resolve', 'open', 'read_text', 'read_bytes', 'write_text',
                     'write_bytes', 'mkdir', 'touch'):
            guard.setattr(Path, name, forbidden)
        control = resolve_control(_base(), seed=4)
        validate_control(control)
        assert control.seed == 4


@pytest.mark.parametrize('seed', [True, -1, 1.5, '1'])
def test_control_rejects_invalid_seed(seed):
    from rpipe.structure.control import resolve_control

    with pytest.raises((TypeError, ValueError)):
        resolve_control(_base(), seed=seed)

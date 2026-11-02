"""The probe compares explicit observed trees without fabricating formal Runs."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_runner,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.fixture
def observed():
    source = Path(__file__).resolve().parents[5] / 'studies/main_probe/flow/write/run.py'
    spec = importlib.util.spec_from_file_location('_probe_observed_comparison', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.compare_observed


def _raw():
    import torch
    def snapshot(step):
        return {'step': step, 'train': {'Loss': .3 / step}, 'test': {'Accuracy': .8},
                'model': {'weight': torch.tensor([.5])},
                'optimizer': {'state': {0: {'step': step}}}, 'scheduler': {'last_epoch': step}}
    old = {1: snapshot(1), 2: snapshot(2)}
    current = deepcopy(old)
    for state in current.values():
        state['model'] = {'model.' + key: value for key, value in state['model'].items()}
    return {'old_snaps': old, 'current_snaps': current, 'old_best': old[1], 'new_best': current[1]}


@pytest.mark.parametrize('damage', [None, 'bool-int', 'missing-optimizer', 'step', 'history'])
def test_observed_state_gates_preserve_inputs_and_scientific_differences(tmp_path, observed, damage):
    import torch
    from rpipe.structure.artifact.comparison import compare_state
    raw = _raw()
    if damage == 'bool-int':
        raw['new_best']['optimizer']['state'][0]['step'] = True
    elif damage == 'missing-optimizer':
        del raw['new_best']['optimizer']
    elif damage == 'step':
        raw['current_snaps'][1]['step'] = 0
    elif damage == 'history':
        raw['current_snaps'][1]['test']['Accuracy'] = .1
    original = deepcopy(raw)
    source, peer = tmp_path / 'observed.pt', tmp_path / 'peer.pt'
    torch.save(raw, source)
    peer.write_bytes(b'peer evidence')
    before = source.read_bytes()
    report = observed(raw)
    assert report['passed'] is (damage is None)
    assert report['atol'] == 1e-6 and report['rtol'] == 1e-5
    assert compare_state(original, raw, atol=0, rtol=0)['passed']
    assert source.read_bytes() == before and peer.read_bytes() == b'peer evidence'


def test_observed_model_prefix_collision_is_rejected_without_discarding_state(observed):
    import torch
    raw = _raw()
    raw['new_best']['model']['weight'] = torch.tensor([9.])
    with pytest.raises(ValueError, match='discard a state key'):
        observed(raw)
    assert set(raw['new_best']['model']) == {'weight', 'model.weight'}

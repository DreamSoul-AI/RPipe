"""Immutable plans and finite enforceable budgets are admission boundaries."""

import math

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_run_spec_effective_configuration_cannot_drift_after_planning(source_study, run_root):
    from rpipe.api import plan

    spec = plan(source_study, run_root=run_root)[0]
    with pytest.raises(TypeError):
        spec.config['algorithm']['num_steps'] = 100
    assert spec.config['algorithm']['num_steps'] == 2


@pytest.mark.parametrize('field,value', [
    ('timeout_seconds', math.nan), ('timeout_seconds', math.inf),
    ('timeout_seconds', -1), ('memory_bytes', -1), ('memory_bytes', True),
])
def test_budget_invalid_limit_is_rejected(field, value):
    from rpipe.structure.contracts import Budget

    with pytest.raises((TypeError, ValueError)):
        Budget(**{field: value}).validate()


@pytest.mark.parametrize('field,value', [('id', value) for value in
    ('', '.', '..', '../outside', '..\\outside', 'nested/run', 'C:escape', 'C:\\escape',
     ' name', 'name ', 'name.', 'CON', 'NUL.txt')] +
    [('version', 'C:'), ('train_id', '../../outside'), ('train_id', 'C:escape')])
def test_run_spec_rejects_windows_drive_components_without_disk_access(
        source_study, run_root, field, value):
    from dataclasses import replace
    from rpipe.api import plan
    from rpipe.structure.contracts import AdmissionError

    spec = plan(source_study, run_root=run_root)[0]
    changes = {field: value}
    if field == 'id':
        config = spec.to_config()
        config['id'] = value
        changes['config'] = config
    with pytest.raises(AdmissionError):
        replace(spec, **changes)
    assert not run_root.exists()


@pytest.mark.parametrize('field,value', [('seed', 777), ('id', 'unrelated-id')])
def test_run_spec_identity_fields_cannot_disagree_with_effective_config(
        source_study, run_root, field, value):
    from dataclasses import replace
    from rpipe.api import plan
    from rpipe.structure.contracts import AdmissionError

    spec = plan(source_study, run_root=run_root)[0]
    with pytest.raises(AdmissionError):
        replace(spec, **{field: value})
    assert not run_root.exists()

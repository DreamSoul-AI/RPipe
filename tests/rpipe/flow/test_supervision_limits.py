"""Observed limits stay explicit and distinct from hard memory budgets."""
import dataclasses

import pytest

from rpipe.structure.control import AdmissionError
from rpipe.flow.context import Budget, SupervisionLimits

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_execution,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_resource_ceilings_have_no_implicit_experiment_values():
    with pytest.raises(TypeError):
        SupervisionLimits()
    limits = SupervisionLimits(1, 2, 1024, 2048)
    limits.validate()
    assert Budget().memory_bytes is None
    assert not hasattr(limits, 'memory_bytes')
    with pytest.raises(dataclasses.FrozenInstanceError):
        limits.rss_bytes = 10


@pytest.mark.parametrize(('field', 'value'), [
    ('run_seconds', None), ('batch_seconds', True), ('run_seconds', 0), ('batch_seconds', -1),
    ('poll_seconds', float('nan')), ('cleanup_seconds', float('inf')),
    ('rss_bytes', '1'), ('output_bytes', -1),
])
def test_invalid_supervision_limits_rejected(field, value):
    values = dict(run_seconds=1, batch_seconds=2, rss_bytes=1024, output_bytes=2048)
    values[field] = value
    with pytest.raises(AdmissionError, match=field):
        SupervisionLimits(**values).validate()


@pytest.mark.parametrize('field', ['rss_bytes', 'output_bytes'])
def test_observed_byte_thresholds_require_integers(field):
    values = dict(run_seconds=1, batch_seconds=2, rss_bytes=1024, output_bytes=2048)
    values[field] = 1.5
    with pytest.raises(AdmissionError, match='integer'):
        SupervisionLimits(**values).validate()

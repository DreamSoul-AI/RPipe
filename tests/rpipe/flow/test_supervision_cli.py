"""Supervision is an explicit execution entry with no scientific defaults."""
import sys
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_cli,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_cli_supervision_requires_resource_ceilings_before_planning(monkeypatch, tmp_path):
    from rpipe.structure import make as planning
    from rpipe.flow.cli import main
    monkeypatch.setattr(planning, 'plan', lambda *a, **k: pytest.fail('must parse explicit ceilings first'))
    with pytest.raises(SystemExit) as caught:
        main(['supervise', str(tmp_path), '--run-root', str(tmp_path / 'output')])
    assert caught.value.code == 2


def test_cli_supervision_does_not_make_or_launch_outside_supervisor(monkeypatch, tmp_path, capsys):
    from rpipe.structure import make as planning
    from rpipe.flow import supervision
    from rpipe.flow.context import SupervisionLimits
    from rpipe.flow.cli import main
    observed = {}
    monkeypatch.setattr(planning, 'plan', lambda *a, **k: ('spec',))
    monkeypatch.setattr(planning, 'publish', lambda *a, **k: pytest.fail('publication must be supervised'))
    monkeypatch.setattr(supervision, 'launch', lambda *a, **k: pytest.fail('dispatch must be supervised'))
    def invoke(specs, **kwargs):
        observed.update(specs=specs, **kwargs)
        return {'status': 'succeeded'}
    monkeypatch.setattr(supervision, 'supervise', invoke)
    args = ['supervise', str(tmp_path), '--run-root', str(tmp_path / 'output'),
            '--evidence-dir', str(tmp_path / 'evidence'), '--run-seconds', '3', '--batch-seconds', '7',
            '--rss-bytes', '65536', '--output-bytes', '4096']
    assert main(args) == 0
    assert observed['limits'] == SupervisionLimits(3, 7, 65536, 4096)
    assert observed['specs'] == ('spec',)
    assert 'succeeded' in capsys.readouterr().out


def test_internal_coordinator_dispatch_is_gated_in_its_own_entry(monkeypatch):
    from rpipe.flow.cli import main
    monkeypatch.setitem(sys.modules, 'rpipe.flow.supervision', SimpleNamespace(coordinator=lambda: 0))
    assert main(['_supervisor-coordinator']) == 0


def test_invalid_limits_rejected_before_source_planning(monkeypatch, tmp_path, capsys):
    from rpipe.structure import make as planning
    from rpipe.flow.cli import main
    monkeypatch.setattr(planning, 'plan', lambda *a, **k: pytest.fail('invalid limits must reject first'))
    assert main(['supervise', str(tmp_path), '--run-root', str(tmp_path / 'output'),
                 '--evidence-dir', str(tmp_path / 'evidence'), '--run-seconds', 'nan',
                 '--batch-seconds', '7', '--rss-bytes', '65536', '--output-bytes', '4096']) == 1
    assert 'run_seconds' in capsys.readouterr().err

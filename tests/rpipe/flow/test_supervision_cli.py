"""Supervision is an explicit execution entry with no scientific defaults."""
import sys
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_cli,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_public_supervision_entry_forwards_explicit_limits(monkeypatch, tmp_path):
    from rpipe import api
    observed = {}
    def invoke(specs, **kwargs):
        observed.update(specs=specs, **kwargs)
        return {'status': 'succeeded'}
    monkeypatch.setitem(sys.modules, 'rpipe.flow.supervision', SimpleNamespace(supervise=invoke))
    limits = api.SupervisionLimits(1, 2, 1024, 4096)
    assert api.supervise((), limits=limits, evidence_dir=tmp_path)['status'] == 'succeeded'
    assert observed['limits'] is limits and observed['evidence_dir'] == tmp_path
    assert observed['retry'] is False and observed['cancellation'] is None


def test_cli_supervision_requires_resource_ceilings_before_planning(monkeypatch, tmp_path):
    from rpipe import api
    from rpipe.flow.cli import main
    monkeypatch.setattr(api, 'plan', lambda *a, **k: pytest.fail('must parse explicit ceilings first'))
    with pytest.raises(SystemExit) as caught:
        main(['supervise', str(tmp_path), '--run-root', str(tmp_path / 'output')])
    assert caught.value.code == 2


def test_cli_supervision_does_not_make_or_launch_outside_supervisor(monkeypatch, tmp_path, capsys):
    from rpipe import api
    from rpipe.flow.cli import main
    observed = {}
    monkeypatch.setattr(api, 'plan', lambda *a, **k: ('spec',))
    monkeypatch.setattr(api, 'make', lambda *a, **k: pytest.fail('publication must be supervised'))
    monkeypatch.setattr(api, 'launch', lambda *a, **k: pytest.fail('dispatch must be supervised'))
    def invoke(specs, **kwargs):
        observed.update(specs=specs, **kwargs)
        return {'status': 'succeeded'}
    monkeypatch.setattr(api, 'supervise', invoke)
    args = ['supervise', str(tmp_path), '--run-root', str(tmp_path / 'output'),
            '--evidence-dir', str(tmp_path / 'evidence'), '--run-seconds', '3', '--batch-seconds', '7',
            '--rss-bytes', '65536', '--output-bytes', '4096']
    assert main(args) == 0
    assert observed['limits'] == api.SupervisionLimits(3, 7, 65536, 4096)
    assert observed['specs'] == ('spec',)
    assert 'succeeded' in capsys.readouterr().out


def test_internal_coordinator_dispatch_is_gated_in_its_own_entry(monkeypatch):
    from rpipe.flow.cli import main
    monkeypatch.setitem(sys.modules, 'rpipe.flow.supervision', SimpleNamespace(coordinator=lambda: 0))
    assert main(['_supervisor-coordinator']) == 0


def test_invalid_limits_rejected_before_source_planning(monkeypatch, tmp_path, capsys):
    from rpipe import api
    from rpipe.flow.cli import main
    monkeypatch.setattr(api, 'plan', lambda *a, **k: pytest.fail('invalid limits must reject first'))
    assert main(['supervise', str(tmp_path), '--run-root', str(tmp_path / 'output'),
                 '--evidence-dir', str(tmp_path / 'evidence'), '--run-seconds', 'nan',
                 '--batch-seconds', '7', '--rss-bytes', '65536', '--output-bytes', '4096']) == 1
    assert 'run_seconds' in capsys.readouterr().err

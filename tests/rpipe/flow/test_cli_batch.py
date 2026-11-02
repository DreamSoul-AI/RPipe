"""Batch exit status and signal ownership cannot invent successful completion."""
import signal

import pytest

from rpipe.flow.context import RunReport

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_cli,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('fault', ['empty', 'short', 'quality', 'evidence', 'failed', 'unknown', 'rejected', None])
def test_launch_exit_requires_every_requested_run_and_accepted_observations(
        source_study, run_root, monkeypatch, capsys, fault):
    from rpipe.flow.cli import main
    from rpipe.flow import supervision
    reports = [RunReport('succeeded', evidence_complete=True) for _ in range(2)]
    if fault == 'empty':
        reports.clear()
    elif fault == 'short':
        reports.pop()
    elif fault == 'quality':
        reports[0].quality = False
    elif fault == 'evidence':
        reports[0].evidence_complete = False
    elif fault:
        reports[0].status = fault
    calls = []
    def launch(specs, **kwargs):
        calls.append((specs, kwargs))
        return tuple(reports)
    monkeypatch.setattr(supervision, 'launch', launch)
    assert main(['launch', str(source_study), '--run-root', str(run_root)]) == (0 if fault is None else 1)
    assert len(calls) == 1 and len(calls[0][0]) == 2
    assert not run_root.exists()
    assert capsys.readouterr().err == ''


@pytest.mark.parametrize('outcome', ['success', 'error', 'interrupt', 'cancel'])
def test_cli_restores_the_exact_previous_signal_handler_after_execution(
        source_study, run_root, monkeypatch, outcome, capsys):
    from rpipe.flow.cli import main
    from rpipe.flow import supervision
    previous, old_calls = signal.getsignal(signal.SIGINT), []
    def old_handler(signum, frame):
        old_calls.append(signum)
    primary = KeyboardInterrupt('owned boundary interrupt')
    def launch(specs, *, cancellation, **kwargs):
        installed = signal.getsignal(signal.SIGINT)
        assert installed is not old_handler
        assert not cancellation.is_set()
        if outcome == 'error':
            raise RuntimeError('launch refused')
        if outcome == 'interrupt':
            raise primary
        if outcome == 'cancel':
            installed(signal.SIGINT, None)
            assert cancellation.is_set()
        return tuple(RunReport('succeeded', evidence_complete=True) for _ in specs)
    monkeypatch.setattr(supervision, 'launch', launch)
    signal.signal(signal.SIGINT, old_handler)
    try:
        argv = ['launch', str(source_study), '--run-root', str(run_root)]
        if outcome == 'interrupt':
            with pytest.raises(KeyboardInterrupt) as caught:
                main(argv)
            assert caught.value is primary
        else:
            assert main(argv) == (0 if outcome == 'success' else 1)
        assert signal.getsignal(signal.SIGINT) is old_handler and old_calls == []
        assert not run_root.exists()
    finally:
        signal.signal(signal.SIGINT, previous)


@pytest.mark.parametrize('command', ['study', 'run-one'])
def test_retired_execution_aliases_are_rejected_before_planning(
        source_study, run_root, monkeypatch, command):
    from rpipe.flow.cli import main
    from rpipe.structure import make
    monkeypatch.setattr(make, 'plan', lambda *a, **k: pytest.fail('retired alias planned work'))
    with pytest.raises(SystemExit) as caught:
        main([command, str(source_study), '--run-root', str(run_root)])
    assert caught.value.code == 2 and not run_root.exists()


@pytest.mark.parametrize('fault', ['incomplete', 'cancelled', None])
def test_supervision_exit_requires_complete_evidence_and_no_pending_interrupt(
        source_study, run_root, tmp_path, monkeypatch, fault, capsys):
    from rpipe.flow.cli import main
    from rpipe.flow import supervision
    def supervise(specs, *, cancellation, **kwargs):
        if fault == 'cancelled':
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            assert cancellation.is_set()
        return {'status': 'succeeded', 'evidence_complete': fault != 'incomplete'}
    monkeypatch.setattr(supervision, 'supervise', supervise)
    args = ['supervise', str(source_study), '--run-root', str(run_root),
            '--evidence-dir', str(tmp_path / 'evidence'), '--run-seconds', '1', '--batch-seconds', '2',
            '--rss-bytes', '65536', '--output-bytes', '4096']
    assert main(args) == (0 if fault is None else 1)
    assert capsys.readouterr().err == '' and not run_root.exists()

"""Supervision failure contracts use owned tiny files/processes, never scientific data."""
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from rpipe.structure.artifact.io import identity, write_json
from rpipe.structure.control import AdmissionError
from rpipe.flow.context import Cancellation, RunReport, SupervisionLimits

pytestmark = [pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.fixture
def module():
    return importlib.import_module('rpipe.flow.supervision')


@pytest.fixture
def specs(source_study, run_root):
    from rpipe.structure.make import plan
    return plan(source_study, run_root=run_root)


@pytest.fixture
def limits():
    return SupervisionLimits(run_seconds=10, batch_seconds=20, rss_bytes=1024 * 1024,
                             output_bytes=1024 * 1024, poll_seconds=0.001, cleanup_seconds=0.1)


def progress(request, specs, *, status='succeeded', quality=True, complete=True, current=False):
    rows = [] if current else [{'index': 0, 'id': specs[0].id, 'identity': identity(specs[0]),
                               'status': status, 'quality': quality, 'evidence_complete': complete,
                               'cleanup_errors': [], 'elapsed_seconds': 0.01}]
    return {'schema': 1, 'request': request['request'], 'identities': [identity(s) for s in specs],
            'completed': rows, 'current': {'index': 0, 'id': specs[0].id,
                                           'started': time.monotonic()} if current else None,
            'done': not current, 'error': None}


def fake_parent(monkeypatch, module, specs, *, scenario=None, assign_error=False,
                sample_error=False, cleanup_error=False, growth=0):
    state = SimpleNamespace(sent=[], killed=False, terminated=False, child=None)

    class Pipe(io.BytesIO):
        def flush(self):
            value = self.getvalue()
            self.seek(0)
            self.truncate()
            for line in value.splitlines():
                body = json.loads(line)
                state.sent.append(body)
                if 'request_path' in body and scenario is not None:
                    scenario(json.loads(Path(body['request_path']).read_text(encoding='utf-8')), state)
                elif body.get('action') == 'cancel':
                    state.child.returncode = 1

    class Child:
        pid = 99999
        returncode = None

        def __init__(self, *args, **kwargs):
            state.child = self
            self.stdin = Pipe()

        def poll(self):
            return self.returncode

        def kill(self):
            state.killed = True
            self.returncode = 1

        def wait(self, timeout):
            return self.returncode

    class Job:
        def assign(self, child):
            if assign_error:
                raise OSError('cannot assign')

        def pids(self):
            return (state.child.pid,) if state.child and state.child.returncode is None else ()

        def rss_bytes(self):
            if sample_error:
                raise OSError('cannot observe')
            return 42

        def terminate(self):
            state.terminated = True
            state.child.returncode = 1

        def wait_empty(self, timeout):
            if cleanup_error:
                raise OSError('cannot confirm cleanup')
            return not self.pids()

        def close(self):
            pass

    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt', environ=os.environ, pathsep=os.pathsep))
    monkeypatch.setattr(module, 'WindowsJob', Job)
    monkeypatch.setattr(module.subprocess, 'Popen', Child)
    monkeypatch.setattr(module, 'output_snapshot', lambda roots: {})
    monkeypatch.setattr(module, 'output_growth', lambda roots, baseline: growth if state.sent else 0)
    return state


@pytest.mark.unit
def test_unsupported_platform_rejects_before_output(module, specs, limits, tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='posix'))
    evidence = tmp_path / 'evidence'
    with pytest.raises(AdmissionError, match='Windows'):
        module.supervise(specs, limits=limits, evidence_dir=evidence)
    assert not evidence.exists() and not specs[0].run_root.exists()


@pytest.mark.unit
@pytest.mark.parametrize('where', ['source', 'run', 'existing'])
def test_evidence_admission_is_read_only(module, specs, limits, tmp_path, monkeypatch, where):
    fake_parent(monkeypatch, module, specs)
    evidence = {'source': specs[0].source_dir / 'evidence',
                'run': specs[0].run_root / 'evidence', 'existing': tmp_path}[where]
    with pytest.raises(AdmissionError):
        module.supervise(specs, limits=limits, evidence_dir=evidence)
    assert not specs[0].run_root.exists()


@pytest.mark.unit
def test_assignment_failure_never_releases_stdin_gate(module, specs, limits, tmp_path, monkeypatch):
    state = fake_parent(monkeypatch, module, specs, assign_error=True)
    report = module.supervise(specs, limits=limits, evidence_dir=tmp_path / 'evidence')
    assert report['trigger']['kind'] == 'observation_failure'
    assert state.killed and state.sent == [] and report['tree_empty']
    assert not specs[0].run_root.exists()


@pytest.mark.unit
@pytest.mark.parametrize('close_error', [False, True])
def test_evidence_directory_race_does_not_overwrite_peer(module, specs, limits, tmp_path, monkeypatch, close_error):
    state = fake_parent(monkeypatch, module, specs)
    if close_error:
        native_job = module.WindowsJob
        class CloseFailure(native_job):
            def close(self):
                raise OSError('cannot close job')
        monkeypatch.setattr(module, 'WindowsJob', CloseFailure)
    evidence = tmp_path / 'raced'
    original_mkdir = Path.mkdir
    def raced_mkdir(path, *args, **kwargs):
        if path == evidence:
            original_mkdir(path)
            (path / 'report.json').write_bytes(b'peer evidence')
            raise FileExistsError('another owner created the directory')
        return original_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', raced_mkdir)
    with pytest.raises(FileExistsError) as caught:
        module.supervise(specs, limits=limits, evidence_dir=evidence)
    assert bool(caught.value.cleanup_errors) is close_error
    assert all(isinstance(error, BaseException) for error in caught.value.cleanup_errors)
    assert (evidence / 'report.json').read_bytes() == b'peer evidence'
    assert state.child is None and not specs[0].run_root.exists()


@pytest.mark.unit
def test_slow_observer_cannot_grant_next_cell_after_batch_deadline(module, specs, limits, tmp_path, monkeypatch):
    def scenario(request, state):
        body = progress(request, specs)
        body['done'] = False
        write_json(Path(request['progress']), body)
    state = fake_parent(monkeypatch, module, specs, scenario=scenario)
    def growth(roots, baseline):
        if state.sent:
            time.sleep(.03)
        return 0
    monkeypatch.setattr(module, 'output_growth', growth)
    limits = SupervisionLimits(run_seconds=1, batch_seconds=.02, rss_bytes=1024 * 1024,
                               output_bytes=1024 * 1024, poll_seconds=.001, cleanup_seconds=.1)
    report = module.supervise(specs, limits=limits, evidence_dir=tmp_path / 'evidence')
    assert report['trigger']['kind'] == 'batch_timeout'
    assert not any(body.get('action') == 'continue' for body in state.sent)


@pytest.mark.unit
def test_final_progress_is_reread_after_child_exit(module, specs, limits, tmp_path, monkeypatch):
    specs = specs[:1]
    def scenario(request, state):
        body = progress(request, specs)
        body['done'] = False
        path = Path(request['progress'])
        write_json(path, body)
        def poll():
            body['done'] = True
            write_json(path, body)
            state.child.returncode = 0
            return 0
        state.child.poll = poll
    fake_parent(monkeypatch, module, specs, scenario=scenario)
    report = module.supervise(specs, limits=limits, evidence_dir=tmp_path / 'evidence')
    assert report['status'] == 'succeeded'


@pytest.mark.unit
def test_cleanup_time_counts_toward_batch_deadline(module, specs, limits, tmp_path, monkeypatch):
    specs = specs[:1]
    def scenario(request, state):
        write_json(Path(request['progress']), progress(request, specs))
        state.child.returncode = 0
    fake_parent(monkeypatch, module, specs, scenario=scenario)
    native_job = module.WindowsJob
    class SlowJob(native_job):
        def wait_empty(self, timeout):
            time.sleep(.15)
            return True
    monkeypatch.setattr(module, 'WindowsJob', SlowJob)
    limits = SupervisionLimits(run_seconds=1, batch_seconds=.1, rss_bytes=1024 * 1024,
                               output_bytes=1024 * 1024, poll_seconds=.001, cleanup_seconds=1)
    report = module.supervise(specs, limits=limits, evidence_dir=tmp_path / 'evidence')
    assert report['status'] == 'failed' and report['trigger']['kind'] == 'batch_timeout'
    assert report['completed'][0]['status'] == 'succeeded'


@pytest.mark.unit
def test_keyboard_interrupt_requests_owned_cancellation(module, specs, limits, tmp_path, monkeypatch):
    state = fake_parent(monkeypatch, module, specs)
    def interrupted(roots, baseline):
        if state.sent:
            raise KeyboardInterrupt()
        return 0
    monkeypatch.setattr(module, 'output_growth', interrupted)
    report = module.supervise(specs, limits=limits, evidence_dir=tmp_path / 'evidence')
    assert report['trigger']['kind'] == 'cancelled' and report['tree_empty']


@pytest.mark.unit
def test_poll_sleep_is_capped_by_nearest_deadline(module, specs, tmp_path, monkeypatch):
    fake_parent(monkeypatch, module, specs)
    limits = SupervisionLimits(run_seconds=1, batch_seconds=.03, rss_bytes=1024 * 1024,
                               output_bytes=1024 * 1024, poll_seconds=1, cleanup_seconds=.1)
    started = time.monotonic()
    report = module.supervise(specs, limits=limits, evidence_dir=tmp_path / 'evidence')
    assert report['trigger']['kind'] == 'batch_timeout' and time.monotonic() - started < .5


@pytest.mark.unit
@pytest.mark.parametrize(('status', 'quality', 'complete'), [
    ('failed', None, False), ('unknown', None, False), ('rejected', None, False), ('succeeded', False, True),
    ('succeeded', True, False)])
def test_first_bad_outcome_stops_without_rewriting_science(module, specs, limits, tmp_path,
                                                           monkeypatch, status, quality, complete):
    def scenario(request, state):
        write_json(Path(request['progress']), progress(request, specs, status=status,
                                                       quality=quality, complete=complete))
        state.child.returncode = 1
    state = fake_parent(monkeypatch, module, specs, scenario=scenario)
    report = module.supervise(specs, limits=limits, evidence_dir=tmp_path / 'evidence')
    assert report['trigger']['kind'] == 'run_outcome'
    assert report['completed'][0]['status'] == status
    assert report['unstarted'] == [spec.id for spec in specs[1:]]
    assert not any(body.get('action') == 'continue' for body in state.sent)


@pytest.mark.unit
@pytest.mark.parametrize('fault', ['timeout', 'observer', 'output', 'cleanup'])
def test_stop_keeps_partial_publication_and_busy_owner(module, specs, limits, tmp_path,
                                                       monkeypatch, fault):
    spec = specs[0]
    def scenario(request, state):
        body = progress(request, specs, current=True)
        body['current']['started'] -= 20
        write_json(Path(request['progress']), body)
        (spec.run_dir / '.execution').mkdir(parents=True)
        write_json(spec.result_path, {'identity': identity(spec), 'status': 'succeeded',
                                     'quality': True, 'evidence_complete': True})
    fake_parent(monkeypatch, module, specs, scenario=scenario,
                sample_error=fault == 'observer', cleanup_error=fault == 'cleanup',
                growth=limits.output_bytes + 7 if fault == 'output' else 0)
    report = module.supervise(specs, limits=limits, evidence_dir=tmp_path / 'evidence')
    assert report['status'] == 'unknown'
    assert report['interrupted']['scientific_status'] == 'succeeded'
    assert report['interrupted']['busy'] and not report['interrupted']['evidence_complete']
    assert (spec.run_dir / '.execution').is_dir()
    assert json.loads(spec.result_path.read_text(encoding='utf-8'))['status'] == 'succeeded'


@pytest.mark.unit
def test_preexisting_cancel_does_not_grant_scientific_work(module, specs, limits, tmp_path, monkeypatch):
    state = fake_parent(monkeypatch, module, specs)
    cancellation = Cancellation()
    cancellation.request()
    report = module.supervise(specs, limits=limits, evidence_dir=tmp_path / 'evidence',
                              cancellation=cancellation)
    assert report['status'] == 'cancelled'
    assert not any('request_path' in body for body in state.sent)
    assert not specs[0].run_root.exists()


@pytest.mark.unit
def test_foreign_progress_fails_closed(module, specs, tmp_path):
    path = tmp_path / 'progress.json'
    write_json(path, progress({'request': 'other'}, specs))
    with pytest.raises(AdmissionError, match='foreign'):
        module._progress(path, 'own', specs)


@pytest.mark.unit
@pytest.mark.parametrize('fault', ['quality', 'incomplete', 'failed', 'rejected', 'exception'])
def test_coordinator_stops_first_failure_using_existing_run_contract(module, specs, tmp_path,
                                                                    monkeypatch, fault):
    request = {'request': 'test', 'specs': [module._spec_body(spec) for spec in specs],
               'progress': str(tmp_path / 'progress.json'), 'retry': False}
    write_json(tmp_path / 'request.json', request)
    gate = {'request': 'test', 'request_path': str(tmp_path / 'request.json')}
    monkeypatch.setattr(module.sys, 'stdin', io.StringIO(json.dumps(gate) + '\n'))
    monkeypatch.setattr(module.threading, 'Thread', lambda **kwargs: SimpleNamespace(start=lambda: None))
    calls = []
    def run(spec, **kwargs):
        calls.append((spec.id, kwargs))
        if fault == 'exception':
            raise RuntimeError('prepare failed')
        return RunReport(fault if fault in {'failed', 'rejected'} else 'succeeded',
                          quality=fault != 'quality', evidence_complete=fault != 'incomplete')
    import importlib
    monkeypatch.setattr(module, 'run', run)
    assert module.coordinator() == 1
    body = json.loads((tmp_path / 'progress.json').read_text(encoding='utf-8'))
    assert len(calls) == 1 and calls[0][1]['retry'] is False
    assert body['done'] and len(body['completed']) == (0 if fault == 'exception' else 1)


@pytest.mark.unit
def test_coordinator_eof_is_a_closed_gate(module, monkeypatch):
    monkeypatch.setattr(module.sys, 'stdin', io.StringIO(''))
    assert module.coordinator() == 1


_TINY_COORDINATOR = '''import json, subprocess, sys, time
from pathlib import Path
import importlib
run_module = importlib.import_module('rpipe.flow.supervision')
from rpipe.structure.artifact.io import identity, write_json
from rpipe.flow.context import RunReport
from rpipe.flow.supervision import coordinator
mode = sys.argv[1]
def run(spec, **kwargs):
    spec.assets_dir.mkdir(parents=True)
    (spec.assets_dir / 'started').write_text(mode)
    owner = spec.run_dir / '.execution'
    owner.mkdir()
    if mode == 'publication':
        write_json(spec.result_path, {'identity': identity(spec), 'status': 'succeeded',
                                     'quality': True, 'evidence_complete': True})
    if mode in ('prepare', 'execute', 'publication'):
        while True: time.sleep(.01)
    if mode == 'output':
        (spec.assets_dir / 'tiny-output').write_bytes(bytes(64 * 1024))
        while True: time.sleep(.01)
    if mode == 'rss':
        memory = bytearray(8 * 1024 * 1024)
        while True: time.sleep(.01)
    if mode == 'orphan':
        child = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(60)'])
        (spec.assets_dir / 'orphan-pid').write_text(str(child.pid))
    owner.rmdir()
    return RunReport('failed' if mode == 'failed' else 'succeeded',
                      quality=mode != 'quality', evidence_complete=mode != 'incomplete')
run_module.run = run
raise SystemExit(coordinator())
'''


def tiny_coordinator(monkeypatch, module, mode):
    native_popen = subprocess.Popen
    def spawn(args, **kwargs):
        return native_popen([sys.executable, '-B', '-c', _TINY_COORDINATOR, mode], **kwargs)
    monkeypatch.setattr(module.subprocess, 'Popen', spawn)
    return native_popen


@pytest.mark.integration
@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object process-tree contract')
@pytest.mark.parametrize('phase', ['prepare', 'execute', 'publication'])
def test_real_windows_deadline_covers_blocked_coordinator_phase(module, specs, tmp_path,
                                                               monkeypatch, phase):
    tiny_coordinator(monkeypatch, module, phase)
    limits = SupervisionLimits(run_seconds=0.25, batch_seconds=8, rss_bytes=128 * 1024 * 1024,
                               output_bytes=1024 * 1024, poll_seconds=.01, cleanup_seconds=.3)
    report = module.supervise(specs, limits=limits, evidence_dir=tmp_path / 'evidence')
    assert report['trigger']['kind'] == 'run_timeout'
    assert report['tree_empty'] and report['status'] == 'unknown'
    assert report['interrupted']['busy']
    assert report['unstarted'] == [spec.id for spec in specs[1:]]
    assert not specs[1].run_dir.exists()
    assert report['interrupted']['scientific_status'] == ('succeeded' if phase == 'publication' else 'missing')


@pytest.mark.integration
@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object process-tree contract')
@pytest.mark.parametrize('mode', ['failed', 'quality', 'incomplete', 'success'])
def test_real_windows_sequential_grants_and_first_failure(module, specs, tmp_path, monkeypatch, mode):
    tiny_coordinator(monkeypatch, module, mode)
    limits = SupervisionLimits(run_seconds=4, batch_seconds=8, rss_bytes=128 * 1024 * 1024,
                               output_bytes=1024 * 1024, poll_seconds=.01, cleanup_seconds=.3)
    report = module.supervise(specs, limits=limits, evidence_dir=tmp_path / 'evidence')
    assert report['tree_empty']
    if mode == 'success':
        assert report['status'] == 'succeeded' and len(report['completed']) == len(specs)
        assert report['unstarted'] == []
    else:
        assert report['trigger']['kind'] == 'run_outcome'
        assert len(report['completed']) == 1 and not specs[1].run_dir.exists()


@pytest.mark.integration
@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object process-tree contract')
def test_real_windows_orphan_is_stopped_and_unrelated_peer_survives(module, specs, tmp_path, monkeypatch):
    native_popen = tiny_coordinator(monkeypatch, module, 'orphan')
    peer = native_popen([sys.executable, '-B', '-c', 'import time; time.sleep(60)'])
    try:
        limits = SupervisionLimits(run_seconds=4, batch_seconds=8, rss_bytes=128 * 1024 * 1024,
                                   output_bytes=1024 * 1024, poll_seconds=.01, cleanup_seconds=.3)
        report = module.supervise(specs, limits=limits, evidence_dir=tmp_path / 'evidence')
        assert report['trigger']['kind'] == 'descendants_remain' and report['tree_empty']
        assert len(report['completed']) == 1 and not specs[1].run_dir.exists()
        assert peer.poll() is None
    finally:
        peer.kill()
        peer.wait(timeout=2)


@pytest.mark.integration
@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object process-tree contract')
@pytest.mark.parametrize('mode', ['output', 'rss'])
def test_real_windows_sampled_limits_stop_tiny_owned_processes(module, specs, tmp_path,
                                                              monkeypatch, mode):
    tiny_coordinator(monkeypatch, module, mode)
    limits = SupervisionLimits(run_seconds=4, batch_seconds=8,
                               rss_bytes=1024 * 1024 if mode == 'rss' else 128 * 1024 * 1024,
                               output_bytes=32 * 1024 if mode == 'output' else 1024 * 1024,
                               poll_seconds=.01, cleanup_seconds=.3)
    report = module.supervise(specs, limits=limits, evidence_dir=tmp_path / 'evidence')
    assert report['trigger']['kind'] == mode + '_limit' and report['tree_empty']
    assert not specs[1].run_dir.exists()


@pytest.mark.integration
@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object process-tree contract')
@pytest.mark.parametrize('fault', ['cancelled', 'observer'])
def test_real_windows_active_cancel_and_observer_failure_retain_owner(module, specs, tmp_path,
                                                                     monkeypatch, fault):
    tiny_coordinator(monkeypatch, module, 'execute')
    cancellation = Cancellation()
    limits = SupervisionLimits(run_seconds=4, batch_seconds=8, rss_bytes=128 * 1024 * 1024,
                               output_bytes=1024 * 1024, poll_seconds=.01, cleanup_seconds=.3)
    marker = specs[0].assets_dir / 'started'
    if fault == 'cancelled':
        def cancel_when_started():
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            cancellation.request()
        thread = threading.Thread(target=cancel_when_started)
        thread.start()
    else:
        native_growth = module.output_growth
        def growth(roots, baseline):
            if marker.exists():
                raise OSError('lost observation')
            return native_growth(roots, baseline)
        monkeypatch.setattr(module, 'output_growth', growth)
    try:
        report = module.supervise(specs, limits=limits, evidence_dir=tmp_path / 'evidence',
                                  cancellation=cancellation)
    finally:
        if fault == 'cancelled':
            thread.join(timeout=6)
    assert report['trigger']['kind'] == ('cancelled' if fault == 'cancelled' else 'observation_failure')
    assert report['tree_empty'] and report['status'] == 'unknown'
    assert (specs[0].run_dir / '.execution').is_dir() and not specs[1].run_dir.exists()


@pytest.mark.integration
@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object process-tree contract')
def test_real_public_run_owns_and_releases_tiny_train_eval(module, tensor_study, run_root, tmp_path):
    from rpipe.structure.make import plan
    from rpipe.structure.artifact.readout import read_run
    specs = plan(tensor_study, run_root=run_root)
    limits = SupervisionLimits(run_seconds=20, batch_seconds=45, rss_bytes=2 * 1024 ** 3,
                               output_bytes=8 * 1024 ** 2, poll_seconds=.02, cleanup_seconds=1)
    report = module.supervise(specs, limits=limits, evidence_dir=tmp_path / 'evidence')
    assert report['status'] == 'succeeded', report
    assert report['evidence_complete'] and report['tree_empty']
    assert len(report['completed']) == 2
    assert all(read_run(spec.ref)['scientific_status'] == 'succeeded' for spec in specs)
    assert all(not (spec.run_dir / '.execution').exists() for spec in specs)
    assert read_run(specs[0].ref)['execute']['steps'] == 2


@pytest.mark.integration
@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object process-tree contract')
def test_real_publication_hook_timeout_preserves_science_and_busy_owner(module, tensor_study,
                                                                       run_root, tmp_path):
    from rpipe.structure.make import plan
    with (tensor_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('flow: {study_phases: true}\n')
    (tensor_study / 'flow').mkdir()
    (tensor_study / 'flow' / 'process.py').write_text(
        'import time\ndef run(ctx):\n    (ctx.layout.assets_dir / "publishing").touch()\n'
        '    while True: time.sleep(.01)\n', encoding='utf-8')
    specs = plan(tensor_study, run_root=run_root)
    limits = SupervisionLimits(run_seconds=20, batch_seconds=45, rss_bytes=2 * 1024 ** 3,
                               output_bytes=8 * 1024 ** 2, poll_seconds=.02, cleanup_seconds=1)
    report = module.supervise(specs, limits=limits, evidence_dir=tmp_path / 'evidence')
    assert (specs[0].assets_dir / 'publishing').is_file(), report
    assert report['trigger']['kind'] == 'run_timeout' and report['status'] == 'unknown'
    assert report['tree_empty'] and report['interrupted']['scientific_status'] == 'succeeded'
    assert (specs[0].run_dir / '.execution').is_dir() and not specs[1].run_dir.exists()


@pytest.mark.integration
def test_launch_unknown_owner_stops_new_dispatch(source_study, run_root, monkeypatch):
    from rpipe.flow.context import RunReport
    from rpipe.structure import make as planning

    specs = planning.plan(source_study, run_root=run_root)
    calls = []

    def unresolved(spec, **kwargs):
        calls.append(spec.id)
        return RunReport('unknown')

    import importlib
    execution = importlib.import_module('rpipe.flow.supervision')
    from rpipe.flow.supervision import launch
    monkeypatch.setattr(execution, 'run', unresolved)
    assert [outcome.status for outcome in launch(specs)] == ['unknown']
    assert calls == [specs[0].id]


@pytest.mark.integration
def test_launch_preserves_explicit_retry_choice(source_study, run_root, monkeypatch):
    from rpipe.flow.context import RunReport
    from rpipe.structure import make as planning

    specs = planning.plan(source_study, run_root=run_root)
    calls = []
    def execute(spec, **kwargs):
        calls.append(kwargs['retry'])
        return RunReport('succeeded', evidence_complete=True)
    import importlib
    execution = importlib.import_module('rpipe.flow.supervision')
    from rpipe.flow.supervision import launch
    monkeypatch.setattr(execution, 'run', execute)
    launch(specs, retry=True)
    assert calls == [True, True]

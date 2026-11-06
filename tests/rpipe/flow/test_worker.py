"""The scientific worker replies after cleanup and never publishes formal results."""
import dataclasses
import os

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_runner,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def _request(spec):
    from rpipe.structure.artifact.io import identity, write_json
    attempt, request_id = 'a' * 32, 'b' * 32
    folder = spec.run_dir / 'attempts' / attempt
    request, response = folder / 'worker-request.json', folder / 'worker-output.json'
    body = {field.name: str(getattr(spec, field.name)) if field.name in {'source_dir', 'run_root'}
            else spec.to_config() if field.name == 'config' else getattr(spec, field.name)
            for field in dataclasses.fields(spec) if field.init}
    write_json(request, {'schema': 1, 'request': request_id, 'attempt': attempt,
                         'identity': identity(spec), 'spec': body,
                         'logging': {'task_id': spec.id, 'attempt_id': attempt}})
    return request, response


def test_scientific_worker_only_writes_its_response_and_diagnostics(
        source_study, run_root, monkeypatch):
    import multiprocessing
    from rpipe.flow import stages, worker
    from rpipe.structure.artifact.io import read_json
    from rpipe.structure.make import plan

    spec = plan(source_study, run_root=run_root)[0]
    request, response = _request(spec)
    closed = []
    class ScienceResource:
        def close(self):
            closed.append(True)
    def compute(ctx):
        assert not hasattr(ctx, 'owner')
        ctx.register_resource(ScienceResource())
        return {'metrics': {'value': 3.0}}
    monkeypatch.setattr(stages, 'compute', compute)
    process = multiprocessing.current_process()
    monkeypatch.setattr(process, 'name', process.name)
    assert worker.worker(request, response) == 0
    observed = read_json(response)
    assert observed['science_closed'] and not observed['unknown_resources']
    assert observed['request'] == 'b' * 32 and observed['attempt'] == 'a' * 32
    assert closed == [True]
    assert not spec.result_path.exists()
    assert not (spec.run_dir / 'execution.json').exists()
    assert not (spec.run_dir / 'config.yaml').exists()
    assert not (spec.run_dir / '.execution').exists()


@pytest.mark.parametrize('science_fails', [False, True])
def test_worker_logger_close_failure_preserves_first_error_and_reports_unknown(
        source_study, run_root, monkeypatch, science_fails):
    import multiprocessing
    from rpipe.flow import stages, worker
    from rpipe.structure.artifact.io import read_json
    from rpipe.structure.make import plan
    from rpipe.structure.system.logger import Logger

    spec = plan(source_study, run_root=run_root)[0]
    request, response = _request(spec)
    def compute(ctx):
        ctx.state['logger'] = Logger(ctx.layout.assets_dir, **ctx.state['logging_context'])
        if science_fails:
            raise ValueError('secret=PRIVATE_PRIMARY_SCIENCE')
        return {'metrics': {'value': 3.0}}
    def close(logger):
        raise OSError('secret=PRIVATE_SECONDARY_LOGGER')
    monkeypatch.setattr(stages, 'compute', compute)
    monkeypatch.setattr(Logger, 'close', close)
    process = multiprocessing.current_process()
    monkeypatch.setattr(process, 'name', process.name)
    assert worker.worker(request, response) == 1
    observed = read_json(response)
    assert observed['unknown_resources'] and not observed['science_closed']
    assert (observed['error']['type'] if observed['error'] else None) == (
        'ValueError' if science_fails else None)
    assert observed['cleanup_errors'][0]['type'] == 'OSError'
    assert 'PRIVATE_PRIMARY_SCIENCE' not in response.read_text(encoding='utf-8')
    assert 'PRIVATE_SECONDARY_LOGGER' not in response.read_text(encoding='utf-8')
    assert not spec.result_path.exists()


@pytest.mark.parametrize('control', ['foreign-request', 'foreign-identity', 'foreign-attempt',
                                  'cancel', 'bad-schema', 'bad-action', 'extra-field'])
def test_worker_cancel_control_is_bound_to_exact_request_and_attempt(
        source_study, run_root, monkeypatch, control):
    import multiprocessing
    from rpipe.flow import stages, worker
    from rpipe.structure.artifact.io import identity, read_json, write_json
    from rpipe.structure.make import plan
    spec = plan(source_study, run_root=run_root)[0]
    request, response = _request(spec)
    marker = request.parent / 'worker-cancel.json'
    body = {'schema': 1, 'action': 'cancel', 'request': 'b' * 32,
            'identity': identity(spec), 'attempt': 'a' * 32}
    if control == 'foreign-request':
        body['request'] = 'c' * 32
    elif control == 'foreign-identity':
        body['identity'] = {**body['identity'], 'source_digest': 'c' * 64}
    elif control == 'foreign-attempt':
        body['attempt'] = 'c' * 32
    elif control == 'bad-schema':
        body['schema'] = True
    elif control == 'bad-action':
        body['action'] = 'stop'
    elif control == 'extra-field':
        body['extra'] = True
    write_json(marker, body)
    before, calls = marker.read_bytes(), []
    def compute(ctx):
        assert not hasattr(ctx, 'cancellation')
        ctx.cancel_check()
        calls.append(True)
        return {'metrics': {'value': 1}}
    monkeypatch.setattr(stages, 'compute', compute)
    process = multiprocessing.current_process()
    monkeypatch.setattr(process, 'name', process.name)
    assert worker.worker(request, response) == (0 if control.startswith('foreign-') else 1)
    observed = read_json(response)
    if control.startswith('foreign-'):
        assert observed['error'] is None and observed['result']['metrics']['value'] == 1
        assert calls == [True]
    else:
        assert observed['error']['type'] == ('Cancelled' if control == 'cancel' else 'AdmissionError')
        assert calls == []
    assert observed['science_closed'] and not observed['unknown_resources']
    assert marker.read_bytes() == before
    assert not spec.result_path.exists() and not (spec.run_dir / 'execution.json').exists()


@pytest.mark.parametrize('science_fails', [False, True])
def test_worker_restores_process_signal_handler_on_success_and_scientific_failure(
        source_study, run_root, monkeypatch, science_fails):
    import multiprocessing
    import os
    import signal
    from rpipe.flow import stages, worker
    from rpipe.structure.make import plan
    spec = plan(source_study, run_root=run_root)[0]
    request, response = _request(spec)
    previous = signal.getsignal(signal.SIGINT)
    def old_handler(signum, frame):
        pytest.fail('worker forwarded an unrequested signal to the previous owner')
    def compute(ctx):
        assert signal.getsignal(signal.SIGINT) == (signal.SIG_IGN if os.name == 'nt' else old_handler)
        if science_fails:
            raise ValueError('scientific failure')
        return {'metrics': {'value': 1}}
    monkeypatch.setattr(stages, 'compute', compute)
    process = multiprocessing.current_process()
    monkeypatch.setattr(process, 'name', process.name)
    signal.signal(signal.SIGINT, old_handler)
    try:
        assert worker.worker(request, response) == int(science_fails)
        assert signal.getsignal(signal.SIGINT) is old_handler
    finally:
        signal.signal(signal.SIGINT, previous)


@pytest.mark.skipif(os.name != 'nt', reason='Windows worker Job closure')
@pytest.mark.parametrize('registered', [False, True])
@pytest.mark.parametrize('primary_failure', [False, True])
def test_windows_run_closes_orphan_descendant_and_never_publishes_success(
        tensor_study, run_root, registered, primary_failure):
    """worker -> exited registered/unregistered root -> live grandchild -> close/write."""
    import ctypes
    import subprocess
    import sys
    import time
    from rpipe.flow.run import run
    from rpipe.structure.make import plan

    with (tensor_study / 'study.yaml').open('a', encoding='utf-8') as stream:
        stream.write('flow: {study_phases: true}\n')
    (tensor_study / 'flow').mkdir()
    script = '''import subprocess, sys
def before(ctx):
    code = "import subprocess,sys; from pathlib import Path; p=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(30)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); Path(sys.argv[1]).write_text(str(p.pid))"
    p = subprocess.Popen([sys.executable, '-B', '-c', code, str(ctx.layout.assets_dir/'orphan.pid')], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if REGISTERED: ctx.register_process(p)
    p.wait(timeout=5)
    if PRIMARY: raise ValueError('scientific primary after root exit')
def run(ctx): pass
'''.replace('REGISTERED', repr(registered)).replace('PRIMARY', repr(primary_failure))
    (tensor_study / 'flow/prepare.py').write_text(script, encoding='utf-8')
    spec = plan(tensor_study, run_root=run_root)[0]
    peer = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(60)'])
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong], ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    def alive(pid):
        handle, code = kernel.OpenProcess(0x1000, False, pid), ctypes.c_ulong()
        try:
            return bool(handle and kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259)
        finally:
            if handle: kernel.CloseHandle(handle)
    pid = None
    try:
        outcome = run(spec)
        pid = int((spec.assets_dir / 'orphan.pid').read_text(encoding='utf-8'))
        assert outcome.status == 'failed'
        assert (outcome.error['type'] if outcome.error else None) == ('ValueError' if primary_failure else None)
        assert any(error['type'] == 'AdmissionError' for error in outcome.cleanup_errors)
        assert not alive(pid), 'Run returned with an owned grandchild still active'
        assert not spec.result_path.exists() and not (spec.run_dir / '.execution').exists()
        assert peer.poll() is None, 'Job cleanup touched an unrelated peer'
    finally:
        # A regression must not strand the deliberately finite counterexample.
        deadline = time.monotonic() + 35
        while pid is not None and alive(pid) and time.monotonic() < deadline:
            time.sleep(.05)
        peer.terminate()
        peer.wait(timeout=5)


@pytest.mark.parametrize('grant', ['', '[]', '{}', '{"schema":true}', '{"action":"cancel"}'])
def test_worker_cannot_compute_before_exact_launch_grant(
        source_study, run_root, monkeypatch, grant):
    import io
    from rpipe.flow import stages, worker
    from rpipe.structure.make import plan
    request, response = _request(plan(source_study, run_root=run_root)[0])
    joined = []
    monkeypatch.setattr(worker, 'join_windows_job', lambda name: joined.append(name))
    monkeypatch.setattr(worker.sys, 'stdin', io.StringIO(grant))
    monkeypatch.setattr(stages, 'compute', lambda ctx: pytest.fail('science entered before grant'))
    with pytest.raises((ValueError, worker.AdmissionError)):
        worker.worker(request, response, job_name='Local\\RPipe-test')
    assert joined == ['Local\\RPipe-test'] and not response.exists()


@pytest.mark.parametrize('fault', ['request', 'identity', 'attempt', 'schema', 'pid', 'not-member', 'cancel'])
def test_parent_does_not_grant_an_unverified_actual_worker(
        source_study, run_root, monkeypatch, fault):
    import io
    from types import SimpleNamespace
    from rpipe.flow import worker
    from rpipe.flow.context import Cancellation, RunContext
    from rpipe.structure.artifact.io import identity, write_json
    from rpipe.structure.make import plan
    spec = plan(source_study, run_root=run_root)[0]
    ctx = RunContext(spec, Cancellation(), None)
    ctx.state.update(attempt='a' * 32, worker_request='b' * 32)
    folder = spec.run_dir / 'attempts' / ctx.state['attempt']
    ready = {'schema': 1, 'request': 'b' * 32, 'attempt': 'a' * 32,
             'identity': identity(spec), 'pid': 123}
    if fault in {'request', 'attempt'}: ready[fault] = 'c' * 32
    elif fault == 'identity': ready[fault] = {'foreign': True}
    elif fault in {'schema', 'pid'}: ready[fault] = True
    write_json(folder / 'worker-ready.json', ready)
    pipe = io.BytesIO()
    ctx.state.update(child=SimpleNamespace(stdin=pipe, poll=lambda: None),
                     worker_job=SimpleNamespace(pids=lambda: () if fault == 'not-member' else (123,)))
    if fault == 'cancel': ctx.cancellation.request()
    with pytest.raises((worker.AdmissionError, worker.Cancelled)):
        worker._grant_windows_worker(ctx, folder, None)
    assert pipe.getvalue() == b'' and not ctx.state.get('worker_gate_granted')


@pytest.mark.skipif(os.name != 'nt', reason='Windows worker Job cleanup failures')
@pytest.mark.parametrize('fault', ['query', 'terminate', 'wait', 'handle-close', 'path'])
def test_windows_worker_cleanup_uncertainty_keeps_owner_and_attempts_handle_close(
        source_study, run_root, monkeypatch, fault):
    from types import SimpleNamespace
    from rpipe.flow import worker
    from rpipe.flow.context import Cancellation, RunContext
    from rpipe.structure.make import plan
    ctx = RunContext(plan(source_study, run_root=run_root)[0], Cancellation(), None)
    calls = []
    def fail():
        raise OSError('bounded cleanup observation failure')
    def pids():
        if fault == 'query': fail()
        return (123,) if fault == 'terminate' else ()
    def handle_close():
        calls.append('close')
        if fault == 'handle-close': fail()
    job = SimpleNamespace(pids=pids, terminate=fail, wait_empty=lambda timeout: fault != 'wait',
                          close=handle_close)
    ctx.state.update(attempt='a' * 32, worker_pid=123, worker_job=job,
                     child=SimpleNamespace(poll=lambda: 0, stdin=None),
                     worker_response_verified=True, worker_response={'science_closed': True},
                     worker_response_attempted=True)
    if fault == 'path':
        monkeypatch.setattr(worker, 'contained', lambda *args: fail())
        with pytest.raises(OSError): worker.close(ctx)
    else:
        worker.close(ctx)
        assert ctx.state['unknown_resources'] and not ctx.state['science_closed']
        assert ctx.state['cleanup_error_records']
    assert calls == ['close']


@pytest.mark.skipif(os.name != 'nt', reason='Windows actual-worker exit window')
def test_cancellation_waits_for_actual_worker_after_launcher_exit(
        source_study, run_root, monkeypatch):
    """A finishing actual worker is not an orphan merely because its launcher exited."""
    from types import SimpleNamespace
    from rpipe.flow import worker
    from rpipe.flow.context import Cancellation, RunContext
    from rpipe.structure.make import plan

    token = Cancellation()
    token.request()
    ctx = RunContext(plan(source_study, run_root=run_root)[0], token, None)
    calls, observations = [], []
    def request_cancel(context):
        assert context is ctx
        calls.append('cancel')
    monkeypatch.setattr(worker, '_request_cancel', request_cancel)
    def pids():
        observations.append(bool(calls))
        return () if calls and len(observations) > 2 else (123,)
    def terminate():
        pytest.fail('cooperative actual worker was mistaken for an orphan')
    job = SimpleNamespace(pids=pids, terminate=terminate,
                          wait_empty=lambda timeout: not pids(), close=lambda: calls.append('close'))
    ctx.state.update(attempt='a' * 32, worker_pid=123, worker_job=job,
                     child=SimpleNamespace(poll=lambda: 0, stdin=None),
                     worker_response_verified=True, worker_response={'science_closed': True},
                     worker_response_attempted=True)

    worker.close(ctx)

    assert calls == ['cancel', 'close']
    assert ctx.state['science_closed'] and not ctx.state['unknown_resources']
    assert not ctx.state['cleanup_error_records']

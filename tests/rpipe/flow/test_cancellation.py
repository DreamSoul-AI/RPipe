"""Attempt-bound cooperative cancellation crosses a real scientific process boundary."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.flow_layer, pytest.mark.module_execution,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


def _study(source, behavior):
    declaration = source / 'study.yaml'
    declaration.write_text('study: cancellation\nseeds: [0, 1]\nflow: {study_phases: true}\n', encoding='utf-8')
    flow = source / 'flow'
    flow.mkdir()
    (flow / 'prepare.py').write_text('''import json, os, time
from pathlib import Path

def before(ctx):
    assets = ctx.layout.assets_dir
    assets.mkdir(parents=True, exist_ok=True)
    class Resource:
        def close(self):
            if BEHAVIOR == 'primary':
                deadline = time.monotonic() + 2
                attempt = Path(ctx.state['logging_context']['attempt_id'])
                marker = ctx.spec.run_dir / 'attempts' / str(attempt) / 'worker-cancel.json'
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
            (assets / 'closed').write_bytes(b'science resource closed')
    ctx.register_resource(Resource())
    assert not hasattr(ctx, 'cancellation')
    (assets / 'ready.json').write_text(json.dumps({'pid': os.getpid()}), encoding='utf-8')
    if BEHAVIOR == 'primary':
        raise ValueError('PRIVATE_FIRST_SCIENCE_ERROR')
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if BEHAVIOR == 'cooperative':
            ctx.cancel_check()
        time.sleep(.01)
    raise TimeoutError('bounded fixture did not receive cancellation')
'''.replace('BEHAVIOR', repr(behavior)), encoding='utf-8')


def _await_file(path, timeout=10):
    deadline = time.monotonic() + timeout
    while not path.is_file():
        if time.monotonic() >= deadline:
            raise AssertionError('owned child did not publish its readiness marker')
        time.sleep(.01)


@pytest.mark.parametrize('behavior', ['cooperative', 'noncooperative', 'primary', 'foreign-marker'])
def test_real_worker_cancellation_preserves_first_error_and_only_owned_resources(
        source_study, run_root, tmp_path, behavior):
    from rpipe.flow.context import Cancellation
    from rpipe.flow.supervision import launch
    from rpipe.structure.artifact.io import read_json, write_json
    from rpipe.structure.make import plan

    _study(source_study, 'cooperative' if behavior == 'foreign-marker' else behavior)
    specs = plan(source_study, run_root=run_root)
    token, controller_errors, marker_bytes = Cancellation(), [], []
    peer = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(30)'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    def cancel():
        try:
            _await_file(specs[0].assets_dir / 'ready.json')
            if behavior == 'foreign-marker':
                execution = read_json(specs[0].run_dir / 'execution.json')
                folder = specs[0].run_dir / 'attempts' / execution['attempt']
                request = read_json(folder / 'worker-request.json')
                path = folder / 'worker-cancel.json'
                foreign = ('0' if request['request'][0] != '0' else '1') + request['request'][1:]
                body = {'schema': 1, 'action': 'cancel', 'request': foreign,
                        'identity': request['identity'], 'attempt': request['attempt']}
                write_json(path, body)
                marker_bytes.append((path, path.read_bytes()))
                time.sleep(.08)
                assert not (specs[0].assets_dir / 'closed').exists(), 'foreign request cancelled science'
            token.request()
        except BaseException as error:
            controller_errors.append(error)
            token.request()
    thread = threading.Thread(target=cancel, daemon=True)
    thread.start()
    try:
        reports = launch(specs, cancellation=token)
        thread.join(2)
        assert not thread.is_alive() and not controller_errors
        assert len(reports) == 1 and reports[0].cancelled
        report = reports[0]
        if behavior in {'noncooperative', 'foreign-marker'}:
            unknown = os.name == 'nt'
            assert report.status == ('unknown' if unknown else
                                     'failed' if behavior == 'foreign-marker' else 'cancelled')
            assert (specs[0].run_dir / '.execution').is_dir() is unknown
            assert not (specs[0].assets_dir / 'closed').exists()
            if behavior == 'foreign-marker':
                assert any(error['type'] == 'AdmissionError' for error in report.cleanup_errors)
        else:
            assert report.status == ('failed' if behavior == 'primary' else 'cancelled')
            assert (specs[0].assets_dir / 'closed').read_bytes() == b'science resource closed'
            assert not (specs[0].run_dir / '.execution').exists()
        assert report.error['type'] == ('ValueError' if behavior == 'primary' else 'Cancelled')
        assert not specs[0].result_path.exists()
        assert not (specs[1].run_dir / 'attempts').exists() and not specs[1].result_path.exists()
        for path, before in marker_bytes:
            assert path.read_bytes() == before
        assert peer.poll() is None, 'cancellation stopped an unrelated peer'
    finally:
        token.request()
        if peer.poll() is None:
            peer.terminate()
        peer.wait(timeout=5)


@pytest.mark.parametrize('command', ['run', pytest.param('supervise', marks=pytest.mark.skipif(
    os.name != 'nt', reason='supervise is an explicit Windows-only execution entry'))])
def test_real_cli_sigint_reaches_child_stops_dispatch_and_restores_handler(
        source_study, run_root, tmp_path, command):
    from rpipe.structure.make import plan
    _study(source_study, 'cooperative')
    specs = plan(source_study, run_root=run_root)
    helper = Path(__file__).resolve().parents[2] / '_data/cli_cancel_worker.py'
    evidence = tmp_path / 'cli-cancel.json'
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    environment['PYTHONPATH'] = (str(Path(__file__).resolve().parents[3] / 'src')
                                 + os.pathsep + environment.get('PYTHONPATH', ''))
    process = subprocess.run([sys.executable, '-B', str(helper), str(source_study), str(run_root),
                              str(specs[0].assets_dir / 'ready.json'), str(evidence), command],
                             env=environment, capture_output=True, text=True,
                             encoding='utf-8', timeout=22)
    assert process.returncode != 0, process.stdout + process.stderr
    proof = json.loads(evidence.read_text(encoding='utf-8'))
    assert proof == {'exit_code': 1, 'old_handler_restored': True, 'old_handler_calls': [],
                     'errors': [], 'interrupt_thread_stopped': True}
    assert (specs[0].assets_dir / 'closed').is_file()
    assert not (specs[0].run_dir / '.execution').exists() and not specs[0].result_path.exists()
    assert not (specs[1].run_dir / 'attempts').exists() and not specs[1].result_path.exists()

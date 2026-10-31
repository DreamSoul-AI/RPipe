"""The public test launcher preserves child diagnostics, arguments and exit status."""
from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]

_spec = importlib.util.spec_from_file_location('rpipe_protocol_entry', Path(__file__).with_name('run.py'))
entry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(entry)


@pytest.mark.parametrize('exit_code', [0, 3])
def test_captured_launcher_keeps_child_environment_output_and_exit(tmp_path, exit_code):
    """A lightweight pytest stand-in exercises two real Python child processes."""
    module = tmp_path / 'pytest'
    module.mkdir()
    (module / '__init__.py').write_text('', encoding='utf-8')
    (module / '__main__.py').write_text(
        "import json,os,sys\n"
        "print('launcher-child='+json.dumps({'token':os.environ.get('RPIPE_ENTRY_PROBE'), 'args':sys.argv[1:], 'prefix':sys.prefix, 'owned':os.environ.get('RPIPE_TEST_OWNER_PID')==str(os.getppid())}),flush=True)\n"
        "print('launcher-child-stderr',file=sys.stderr,flush=True)\n"
        "from pathlib import Path\n"
        "p=Path(os.environ['RPIPE_TEST_RUN_DIR'])/'manifest.json'\n"
        "m=json.loads(p.read_text(encoding='utf-8')); m['collection_complete']=True\n"
        "p.write_text(json.dumps(m),encoding='utf-8')\n"
        f"raise SystemExit({exit_code})\n", encoding='utf-8',
    )
    env = dict(os.environ)
    env['PYTHONPATH'] = str(tmp_path)
    env['RPIPE_ENTRY_PROBE'] = 'known-test-value'
    launcher = Path(__file__).resolve().parent / 'run.py'
    result = subprocess.run([sys.executable, str(launcher), '--core', '--', '--tb=no'],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == exit_code
    line = next(line for line in result.stdout.splitlines() if line.startswith('launcher-child='))
    observed = json.loads(line.removeprefix('launcher-child='))
    assert observed['token'] == 'known-test-value'
    assert observed['owned'] and observed['prefix'] == sys.prefix
    assert observed['args'] == ['-m', '(unit and not slow and not external and not gpu)',
                                '--cost-class', 'c1', '--cost-class', 'c2', '--tb=no']
    assert 'launcher-child-stderr' in result.stderr


@pytest.mark.parametrize('option,value', [('--timeout-seconds', 'nan'), ('--budget-seconds', '0'), ('--retries', '-1')])
def test_launcher_rejects_invalid_budget_before_creating_process_or_evidence(tmp_path, monkeypatch, option, value):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid budgets must be rejected before process launch')
    monkeypatch.setattr(entry.subprocess, 'Popen', forbidden)
    with pytest.raises(SystemExit) as caught:
        entry.main(['--results-root', str(tmp_path), option, value])
    assert caught.value.code == 2
    assert not list(tmp_path.iterdir())


def test_total_budget_caps_batch_timeout_and_stops_retry_after_exhaustion(tmp_path, monkeypatch):
    ticks = iter([0, .2, .4, 2, 2.1])
    monkeypatch.setattr(entry.time, 'monotonic', lambda: next(ticks))
    calls = []
    class Child:
        def __init__(self, *args, **kwargs):
            self.pid = 1
            self.stopped = False
            calls.append(kwargs)
        def wait(self, timeout=None):
            if self.stopped:
                return 0
            if timeout is not None:
                assert timeout == pytest.approx(.8)
                raise subprocess.TimeoutExpired('owned fixture child', timeout)
            return 0
        def poll(self):
            return None
        def kill(self):
            calls.append('owned-child-killed')
            self.stopped = True
    monkeypatch.setattr(entry.subprocess, 'Popen', Child)
    code = entry.main(['--results-root', str(tmp_path), '--timeout-seconds', '10', '--budget-seconds', '1', '--retries', '3'])
    assert code == 1 and len(calls) == 2
    manifest_path, = tmp_path.glob('*/manifest.json')
    manifest = json.loads(manifest_path.read_text())
    assert len(manifest['attempts']) == 1 and manifest['attempts'][0]['status'] == 'timeout'


@pytest.mark.parametrize('mode,exit_code', [('timeout', 1), ('cleanup_error', 1), ('interrupted', 2), ('launch_error', 3)])
def test_abnormal_batch_never_retries_or_claims_descendant_cleanup(tmp_path, monkeypatch, mode, exit_code):
    children = []
    class Child:
        def __init__(self, *args, **kwargs):
            children.append(self)
            self.stopped = False
            if mode == 'launch_error':
                raise OSError('controlled launch error')
        def wait(self, timeout=None):
            if self.stopped:
                return 0
            if mode == 'interrupted':
                raise KeyboardInterrupt()
            raise subprocess.TimeoutExpired('owned fixture child', timeout)
        def poll(self):
            return 0 if self.stopped else None
        def kill(self):
            if mode == 'cleanup_error':
                raise OSError('controlled root-stop failure')
            self.stopped = True
    monkeypatch.setattr(entry.subprocess, 'Popen', Child)
    assert entry.main(['--results-root', str(tmp_path), '--retries', '1']) == exit_code
    assert len(children) == 1
    manifest_path, = tmp_path.glob('*/manifest.json')
    manifest = json.loads(manifest_path.read_text())
    attempt, = manifest['attempts']
    assert attempt['cleanup']['descendants'] == 'unconfirmed'
    assert attempt['cleanup']['all_resources_released'] is None
    if mode == 'cleanup_error':
        assert attempt['cleanup']['pytest_process'] == 'unknown'
        assert 'controlled root-stop failure' in attempt['cleanup']['error']

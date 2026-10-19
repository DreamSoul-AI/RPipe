"""The public test launcher preserves child diagnostics, arguments and exit status."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]


@pytest.mark.parametrize('exit_code', [0, 3])
def test_captured_launcher_keeps_child_environment_output_and_exit(tmp_path, exit_code):
    """A lightweight pytest stand-in exercises two real Python child processes."""
    module = tmp_path / 'pytest'
    module.mkdir()
    (module / '__init__.py').write_text('', encoding='utf-8')
    (module / '__main__.py').write_text(
        "import json,os,sys\n"
        "print('launcher-child='+json.dumps({'token':os.environ.get('RPIPE_ENTRY_PROBE'), 'args':sys.argv[1:]}),flush=True)\n"
        "print('launcher-child-stderr',file=sys.stderr,flush=True)\n"
        f"raise SystemExit({exit_code})\n", encoding='utf-8',
    )
    env = dict(os.environ)
    env['PYTHONPATH'] = str(tmp_path)
    env['RPIPE_ENTRY_PROBE'] = 'known-test-value'
    launcher = Path(__file__).resolve().parent / 'run.py'
    result = subprocess.run([sys.executable, str(launcher), '--core', '--', '--tb=no'],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == exit_code
    line = next(line for line in result.stdout.splitlines() if line.startswith('launcher-child='))
    observed = json.loads(line.removeprefix('launcher-child='))
    assert observed['token'] == 'known-test-value'
    assert observed['args'] == ['-m', '(unit and not slow and not external and not gpu)',
                                '--cost-class', 'c1', '--cost-class', 'c2', '--tb=no']
    assert 'launcher-child-stderr' in result.stderr


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf'])
def test_deadline_rejects_nonpositive_and_nonfinite_before_start(tmp_path, value):
    launcher = Path(__file__).resolve().parent / 'run.py'
    result = subprocess.run([sys.executable, str(launcher), '--timeout', value],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 2 and '--timeout must be finite and > 0' in result.stderr


@pytest.mark.parametrize('exit_code', [0, 3])
def test_evidence_write_failure_preserves_existing_nonzero_child_exit(tmp_path, exit_code):
    """Fault at the parent persistence boundary, after a real child has exited."""
    module = tmp_path / 'pytest'
    module.mkdir()
    (module / '__init__.py').write_text('', encoding='utf-8')
    (module / '__main__.py').write_text(f'raise SystemExit({exit_code})', encoding='utf-8')
    tests = Path(__file__).resolve().parent
    driver = (
        'import sys\nfrom pathlib import Path\n'
        f'sys.path.insert(0,{str(tests)!r})\nimport run\n'
        f'run.__file__={str(tmp_path / "tests/run.py")!r}\n'
        'original=Path.write_text\n'
        'def write(path,*a,**kw):\n'
        "    if 'execution.json' in path.name:raise OSError('injected evidence write failure')\n"
        '    return original(path,*a,**kw)\n'
        'Path.write_text=write\nraise SystemExit(run.main(["--all"]))\n')
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = subprocess.run([sys.executable, '-c', driver], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == (3 if exit_code else 1)
    assert 'injected evidence write failure' in result.stderr
    if exit_code:
        assert 'Test evidence persistence failed' in result.stderr


def test_native_pytest_exit_stops_worker_holding_captured_pipes(tmp_path):
    """Real pytest finishes; its worker must not outlive the launcher's ownership."""
    tests = tmp_path / 'tests'
    tests.mkdir()
    source = Path(__file__).resolve().parent.parent
    for name in ['tests/run.py', 'tests/_process_tree.py', 'tests/conftest.py', 'pyproject.toml']:
        import shutil
        shutil.copy2(source / name, tmp_path / name)
    worker = 'import time; time.sleep(3); print("UNOWNED_PIPE_WORKER_SURVIVED",flush=True)'
    (tests / 'test_minimal.py').write_text(
        'import subprocess,sys\nimport pytest\n'
        "pytestmark=[pytest.mark.unit,pytest.mark.content,pytest.mark.p1,"
        "pytest.mark.cost(cost_class='c1'),pytest.mark.result_type('categorical',detail='summary')]\n"
        'def test_worker():\n'
        f'    subprocess.Popen([sys.executable,"-u","-c",{worker!r}])\n'
        '    print("parent-pytest-exiting",flush=True)\n', encoding='utf-8')
    env = dict(os.environ, CUDA_VISIBLE_DEVICES='-1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    start = time.monotonic()
    result = subprocess.run(
        [sys.executable, 'tests/run.py', '--timeout', '5', '--core', '--', 'tests/test_minimal.py', '-s'],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'parent-pytest-exiting' in result.stdout and 'UNOWNED_PIPE_WORKER_SURVIVED' not in result.stdout
    assert time.monotonic() - start < 10
    directory = next((tmp_path / '.tmp/test-results').iterdir())
    execution = json.loads((directory / 'execution.json').read_text(encoding='utf-8'))
    assert execution['process_returncode'] == 0 and execution['cleanup_confirmed']
    assert execution['owned_processes_before_cleanup'] >= 1
    assert execution['cleanup_errors'] == []


@pytest.mark.skipif(os.name != 'nt', reason='Windows suspended-start ownership')
@pytest.mark.parametrize('phase', ['assign', 'resume'])
def test_windows_startup_failure_never_runs_the_suspended_child(tmp_path, phase):
    module = tmp_path / 'pytest'
    module.mkdir()
    (module / '__init__.py').write_text('', encoding='utf-8')
    (module / '__main__.py').write_text(
        'from pathlib import Path\nPath("UNEXPECTED_EXECUTION").write_text("ran")', encoding='utf-8')
    tests = Path(__file__).resolve().parent
    driver = (
        'import sys\n'
        f'sys.path.insert(0,{str(tests)!r})\nimport run,_process_tree\n'
        f'run.__file__={str(tmp_path / "tests/run.py")!r}\n'
        'def rejected(self,proc):\n'
        f'    if {phase!r}=="resume":self._check(self.api.AssignProcessToJobObject(self.handle,int(proc._handle)))\n'
        f'    raise PermissionError("controlled {phase} denial")\n'
        '_process_tree._WindowsJob.assign_and_resume=rejected\n'
        'raise SystemExit(run.main(["--timeout","5","--all"]))\n')
    result = subprocess.run([sys.executable, '-c', driver], cwd=tmp_path,
                            env=dict(os.environ, PYTHONPATH=str(tmp_path), CUDA_VISIBLE_DEVICES='-1'),
                            capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode != 0 and f'controlled {phase} denial' in result.stderr
    assert not (tmp_path / 'UNEXPECTED_EXECUTION').exists()
    execution = json.loads(next((tmp_path / '.tmp/test-results').glob('*/execution.json')).read_text())
    assert execution['status'] == 'error' and execution['cleanup_confirmed']
    assert execution['process_returncode'] is not None and not execution['cleanup_errors']


@pytest.mark.parametrize('kind', ['timeout', 'interrupt', 'nonzero'])
@pytest.mark.parametrize('failure_phase', ['process-tree', 'ownership-handle'])
def test_primary_exit_survives_cleanup_failure_and_closed_stderr(tmp_path, monkeypatch, kind, failure_phase):
    import run

    primary = (subprocess.TimeoutExpired(['owned'], 1) if kind == 'timeout'
               else KeyboardInterrupt('controlled first interrupt'))

    class Process:
        pid = 123456
        returncode = None

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

        def wait(self, timeout):
            return self.returncode

    class Tree:
        proc = Process()
        job = None

        def start(self, *args, **kwargs):
            return self.proc

        def wait(self, timeout):
            if kind == 'nonzero':
                self.proc.returncode = 7
                return 7
            raise primary

        def terminate(self, timeout):
            if failure_phase == 'process-tree':
                raise PermissionError('controlled tree cleanup failure')
            return 0

        def close(self):
            if failure_phase == 'ownership-handle':
                raise OSError('controlled ownership handle close failure')

    class ClosedStderr:
        def write(self, value):
            raise BrokenPipeError('controlled closed diagnostic pipe')

        def flush(self):
            pass

    monkeypatch.setattr(run, 'ProcessTree', Tree)
    monkeypatch.setattr(run, '__file__', str(tmp_path / 'tests/run.py'))
    monkeypatch.setattr(sys, 'stderr', ClosedStderr())
    if kind == 'interrupt':
        with pytest.raises(KeyboardInterrupt) as caught:
            run._run(['owned'], 1)
        assert caught.value is primary
    else:
        assert run._run(['owned'], 1) == (124 if kind == 'timeout' else 7)
    execution = json.loads(next((tmp_path / '.tmp/test-results').glob('*/execution.json')).read_text())
    assert not execution['cleanup_confirmed']
    assert execution['cleanup_errors'][0]['phase'] == failure_phase
    assert 'BrokenPipeError' in execution['diagnostic_errors'][0]
    assert execution['status'] == {'nonzero': 'completed', 'timeout': 'timeout', 'interrupt': 'interrupted'}[kind]


def test_manifest_atomic_update_keeps_old_manifest_on_hard_exit(tmp_path):
    manifest = tmp_path / 'manifest.json'
    original = b'{"run_id":"known-old","collected_test_ids":["existing"]}\n'
    manifest.write_bytes(original)
    tests = Path(__file__).resolve().parent
    driver = (
        'import os,sys\nfrom pathlib import Path\n'
        f'sys.path.insert(0,{str(tests)!r})\nimport conftest\n'
        'native=Path.write_text\n'
        'def interrupted(path,*args,**kwargs):\n'
        '    native(path,"partial update",encoding="utf-8")\n    os._exit(33)\n'
        'Path.write_text=interrupted\n'
        f'conftest._write_manifest(Path({str(manifest)!r}),{{"run_id":"new"}})\n')
    result = subprocess.run([sys.executable, '-c', driver], cwd=tmp_path,
                            capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 33 and manifest.read_bytes() == original
    partial, = tmp_path.glob('.manifest.json-*.tmp')
    assert partial.read_text() == 'partial update'


@pytest.mark.parametrize('exit_code', [0, 7])
def test_report_failure_with_closed_stderr_keeps_original_error_and_child_exit(tmp_path, monkeypatch, exit_code):
    import conftest
    import run

    failure = OSError('controlled report persistence failure')

    class Process:
        pid = 123456
        returncode = exit_code

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            return self.returncode

    class Tree:
        proc = Process()
        job = None

        def start(self, *args, **kwargs):
            return self.proc

        def wait(self, timeout):
            return exit_code

        def terminate(self, timeout):
            return 0

        def close(self):
            pass

    class ClosedStderr:
        def write(self, value):
            raise BrokenPipeError('controlled closed diagnostic pipe')

        def flush(self):
            pass

    native_save = run._save_execution

    def save(directory, execution):
        native_save(directory, execution)
        (directory / 'manifest.json').write_text('{"run_id":"controlled"}', encoding='utf-8')
        execution['manifest_available'] = True

    def rejected(*args):
        raise failure

    monkeypatch.setattr(run, 'ProcessTree', Tree)
    monkeypatch.setattr(run, '__file__', str(tmp_path / 'tests/run.py'))
    monkeypatch.setattr(run, '_save_execution', save)
    monkeypatch.setattr(conftest, '_write_report', rejected)
    monkeypatch.setattr(sys, 'stderr', ClosedStderr())
    if exit_code:
        assert run._run(['owned'], 1) == exit_code
    else:
        with pytest.raises(OSError) as caught:
            run._run(['owned'], 1)
        assert caught.value is failure
    execution = json.loads(next((tmp_path / '.tmp/test-results').glob('*/execution.json')).read_text())
    assert execution['exit_code'] == (exit_code or 1)
    assert execution['process_returncode'] == exit_code
    assert execution['persistence_errors'] == [repr(failure)]
    assert 'BrokenPipeError' in execution['diagnostic_errors'][0]

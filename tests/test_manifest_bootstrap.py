"""Native test entry completes when optional local Git metadata is unreadable."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('head,ci_sha', [(b'\xff', None), (b'ref:', None), (b'\xff', 'a' * 40)])
def test_native_test_entry_keeps_optional_corrupt_metadata(tmp_path, head, ci_sha):
    """Copied native tests/run.py → real pytest session → executed fixture and null/explicit SHA manifest."""
    source = Path(__file__).resolve().parent.parent
    (tmp_path / 'tests').mkdir()
    for name in ['tests/run.py', 'tests/_process_tree.py', 'tests/conftest.py', 'pyproject.toml']:
        shutil.copy2(source / name, tmp_path / name)
    (tmp_path / '.git').mkdir()
    (tmp_path / '.git/HEAD').write_bytes(head)
    (tmp_path / 'tests/test_minimal.py').write_text(
        "from pathlib import Path\nimport pytest\n"
        "pytestmark=[pytest.mark.unit,pytest.mark.content,pytest.mark.p1,"
        "pytest.mark.cost(cost_class='c1'),pytest.mark.result_type('categorical',detail='summary')]\n"
        "def test_executes():\n    Path('EXECUTED').write_text('ok')\n", encoding='utf-8')
    env = os.environ.copy()
    env.pop('GITHUB_SHA', None)
    if ci_sha is not None:
        env['GITHUB_SHA'] = ci_sha
    child = subprocess.run(
        [sys.executable, 'tests/run.py', '--core', '--', 'tests/test_minimal.py',
         '--basetemp', str(tmp_path / 'p')], cwd=tmp_path, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30, check=False)
    assert child.returncode == 0, child.stdout
    assert (tmp_path / 'EXECUTED').read_text() == 'ok'
    manifests = list((tmp_path / '.tmp/test-results').glob('*/manifest.json'))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert manifest['git_sha'] == ci_sha
    assert manifest['basetemp'] == str(tmp_path / 'p')
    events = [json.loads(line) for line in (manifests[0].parent / 'events.jsonl').read_text().splitlines()]
    assert [(event['phase'], event['status']) for event in events] == [
        ('setup', 'passed'), ('call', 'passed'), ('teardown', 'passed')]



@pytest.mark.parametrize('kind', ['valid', 'high', 'low', 'mixed'])
@pytest.mark.parametrize('when', ['call', 'setup'])
def test_native_failure_evidence_preserves_unicode_and_peer(tmp_path, kind, when):
    source = Path(__file__).resolve().parent.parent
    (tmp_path / 'tests').mkdir()
    for name in ['tests/run.py', 'tests/_process_tree.py', 'tests/conftest.py', 'pyproject.toml']:
        shutil.copy2(source / name, tmp_path / name)
    expressions = {'valid': "'normal'", 'high': 'chr(0xD800)',
                   'low': 'chr(0xDCFF)', 'mixed': "chr(0xD800) + '/' + chr(0xDCFF)"}
    text = '正常诊断🙂 ' + {'valid': 'normal', 'high': chr(0xD800),
                          'low': chr(0xDCFF), 'mixed': chr(0xD800) + '/' + chr(0xDCFF)}[kind]
    code = (
        "from pathlib import Path\nimport pytest\n"
        "pytestmark=[pytest.mark.unit,pytest.mark.content,pytest.mark.p1,"
        "pytest.mark.cost(cost_class='c1'),pytest.mark.result_type('categorical',detail='summary')]\n"
        "TEXT = '正常诊断🙂 ' + " + expressions[kind] + "\n")
    if when == 'setup':
        code += ("@pytest.fixture\ndef rejected():\n    raise ValueError(TEXT)\n"
                 "def test_01_failure(rejected):\n    pass\n")
    else:
        code += "def test_01_failure():\n    raise AssertionError(TEXT)\n"
    code += "def test_02_peer():\n    Path('EXECUTED_PEER').write_text('ok')\n"
    (tmp_path / 'tests/test_minimal.py').write_text(code, encoding='utf-8')
    env = os.environ.copy()
    env.pop('GITHUB_SHA', None)
    env.update(PYTHONIOENCODING='utf-8:backslashreplace', CUDA_VISIBLE_DEVICES='-1', MPLBACKEND='Agg')
    child = subprocess.run(
        [sys.executable, 'tests/run.py', '--core', '--', 'tests/test_minimal.py',
         '--basetemp', str(tmp_path / 'p')], cwd=tmp_path, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding='utf-8', errors='backslashreplace', timeout=30, check=False)
    assert child.returncode == 1, child.stdout
    assert 'INTERNALERROR' not in child.stdout
    assert (tmp_path / 'EXECUTED_PEER').read_text() == 'ok'
    manifests = list((tmp_path / '.tmp/test-results').glob('*/manifest.json'))
    assert len(manifests) == 1
    raw = (manifests[0].parent / 'events.jsonl').read_bytes()
    events = [json.loads(line) for line in raw.decode('utf-8').splitlines()]
    failures = [event for event in events if event['status'] == 'failed']
    assert len(failures) == 1 and failures[0]['failure']['when'] == when
    assert text in failures[0]['failure']['longrepr']
    assert any(event['test_id'].endswith('test_02_peer') and event['phase'] == 'call'
               and event['status'] == 'passed' for event in events)
    assert '正常诊断🙂 '.encode() in raw
    report = (manifests[0].parent / 'report.md').read_text(encoding='utf-8')
    assert 'exit_status: 1' in report


@pytest.mark.parametrize('phase', ['teardown', 'setup_skip'])
def test_native_phase_evidence_and_report_rebuild(tmp_path, phase):
    """Real pytest phases survive process exit and reconstruct the same report."""
    source = Path(__file__).resolve().parent.parent
    (tmp_path / 'tests').mkdir()
    for name in ['tests/run.py', 'tests/_process_tree.py', 'tests/conftest.py', 'pyproject.toml']:
        shutil.copy2(source / name, tmp_path / name)
    code = (
        "import pytest\n"
        "pytestmark=[pytest.mark.unit,pytest.mark.content,pytest.mark.p1,"
        "pytest.mark.cost(cost_class='c1'),pytest.mark.result_type('categorical',detail='summary')]\n"
        "@pytest.fixture\ndef guarded():\n")
    if phase == 'teardown':
        code += "    yield\n    raise RuntimeError('cleanup refused')\n"
    else:
        code += "    pytest.skip('setup not ready')\n"
    code += "def test_01_guarded(guarded):\n    pass\ndef test_02_peer():\n    pass\n"
    (tmp_path / 'tests/test_minimal.py').write_text(code, encoding='utf-8')
    env = dict(os.environ, PYTHONIOENCODING='utf-8:backslashreplace',
               CUDA_VISIBLE_DEVICES='-1', MPLBACKEND='Agg', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    child = subprocess.run(
        [sys.executable, 'tests/run.py', '--core', '--', 'tests/test_minimal.py',
         '--basetemp', str(tmp_path / 'p')], cwd=tmp_path, env=env,
        capture_output=True, text=True, encoding='utf-8', timeout=30, check=False)
    assert child.returncode == (1 if phase == 'teardown' else 0), child.stdout + child.stderr
    manifests = list((tmp_path / '.tmp/test-results').glob('*/manifest.json'))
    assert len(manifests) == 1
    directory = manifests[0].parent
    raw = (directory / 'events.jsonl').read_bytes()
    events = [json.loads(line) for line in raw.splitlines()]
    guarded = [event for event in events if 'test_01_guarded' in event['test_id']]
    if phase == 'teardown':
        assert [(event['phase'], event['status']) for event in guarded] == [
            ('setup', 'passed'), ('call', 'passed'), ('teardown', 'failed')]
        assert 'cleanup refused' in guarded[-1]['failure']['longrepr']
        expected = '| failed | 1 |'
    else:
        assert [(event['phase'], event['status']) for event in guarded] == [
            ('setup', 'skipped'), ('teardown', 'passed')]
        assert 'setup not ready' in guarded[0]['reason']
        expected = '| skipped | 1 |'
    report_path = directory / 'report.md'
    report = report_path.read_bytes()
    assert expected in report.decode('utf-8')
    assert '| passed | 1 |' in report.decode('utf-8')
    assert ('cleanup refused' if phase == 'teardown' else 'setup not ready') in report.decode('utf-8')
    report_path.write_text('replace from persisted evidence', encoding='utf-8')
    rebuilt = subprocess.run(
        [sys.executable, 'tests/run.py', '--report-only', str(directory)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=False)
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    assert report_path.read_bytes() == report
    assert (directory / 'events.jsonl').read_bytes() == raw


def test_default_parallel_sessions_keep_separate_temporary_roots(tmp_path):
    """Two real default invocations cannot clean each other's pytest directory."""
    source = Path(__file__).resolve().parent.parent
    (tmp_path / 'tests').mkdir()
    for name in ['tests/run.py', 'tests/_process_tree.py', 'tests/conftest.py', 'pyproject.toml']:
        shutil.copy2(source / name, tmp_path / name)
    (tmp_path / 'tests/test_minimal.py').write_text(
        "import json,os,time\nfrom pathlib import Path\nimport pytest\n"
        "pytestmark=[pytest.mark.unit,pytest.mark.content,pytest.mark.p1,"
        "pytest.mark.cost(cost_class='c1'),pytest.mark.result_type('categorical',detail='summary')]\n"
        "def test_uses_temporary(tmp_path,tmp_path_factory):\n"
        "    owned=tmp_path/'owned.txt'\n    owned.write_text('owned')\n"
        "    Path('base-'+os.environ['SESSION_ID']+'.json').write_text(json.dumps(str(tmp_path_factory.getbasetemp())))\n"
        "    time.sleep(.25)\n    assert owned.read_text()=='owned'\n", encoding='utf-8')
    children = []
    try:
        for session_id in ('one', 'two'):
            env = dict(os.environ, SESSION_ID=session_id, CUDA_VISIBLE_DEVICES='-1',
                       PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONIOENCODING='utf-8:backslashreplace')
            children.append(subprocess.Popen(
                [sys.executable, 'tests/run.py', '--core', '--', 'tests/test_minimal.py'],
                cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT))
        for child in children:
            output, _ = child.communicate(timeout=20)
            assert child.returncode == 0, output.decode('utf-8', errors='replace')
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    bases = [Path(json.loads((tmp_path / f'base-{name}.json').read_text())) for name in ('one', 'two')]
    assert bases[0] != bases[1]
    for base in bases:
        assert base.is_relative_to(tmp_path / '.tmp/test-results')
        assert base.is_dir()


def _process_running(pid):
    if os.name == 'nt':
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            assert ctypes.windll.kernel32.GetLastError() == 87
            return False
        try:
            code = ctypes.c_ulong()
            assert ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize('phase', ['call', 'teardown'])
def test_native_deadline_stops_owned_tree_and_rebuilds_partial_report(tmp_path, phase):
    source = Path(__file__).resolve().parent.parent
    (tmp_path / 'tests').mkdir()
    for name in ['tests/run.py', 'tests/_process_tree.py', 'tests/conftest.py', 'pyproject.toml']:
        shutil.copy2(source / name, tmp_path / name)
    (tmp_path / 'tests/test_minimal.py').write_text(
        "import json,subprocess,sys,time\nfrom pathlib import Path\nimport pytest\n"
        "pytestmark=[pytest.mark.unit,pytest.mark.content,pytest.mark.p1,"
        "pytest.mark.cost(cost_class='c1'),pytest.mark.result_type('categorical',detail='summary')]\n"
        "def hang():\n"
        "    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
        "    Path('descendant.json').write_text(json.dumps(child.pid))\n"
        "    print('deadline probe ready',flush=True)\n    time.sleep(60)\n"
        "@pytest.fixture\ndef guarded():\n    yield\n"
        f"    {'hang()' if phase == 'teardown' else 'pass'}\n"
        "def test_01_hanging(guarded):\n"
        f"    {'hang()' if phase == 'call' else 'pass'}\n"
        "def test_02_not_run():\n    Path('UNEXPECTED_PEER').write_text('executed')\n", encoding='utf-8')
    env = dict(os.environ, CUDA_VISIBLE_DEVICES='-1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
               PYTHONIOENCODING='utf-8:backslashreplace')
    try:
        child = subprocess.run(
            [sys.executable, 'tests/run.py', '--timeout', '2', '--core', '--', 'tests/test_minimal.py', '-s'],
            cwd=tmp_path, env=env, capture_output=True, text=True, encoding='utf-8', timeout=25, check=False)
        assert child.returncode == 124, child.stdout + child.stderr
        assert 'deadline probe ready' in child.stdout
        assert not _process_running(json.loads((tmp_path / 'descendant.json').read_text()))
        assert not (tmp_path / 'UNEXPECTED_PEER').exists()
        directory = next((tmp_path / '.tmp/test-results').iterdir())
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        assert len(manifest['collected_test_ids']) == 2
        assert 'exit_status' not in manifest
        execution = json.loads((directory / 'execution.json').read_text(encoding='utf-8'))
        assert execution['status'] == 'timeout' and execution['exit_code'] == 124
        assert execution['cleanup_confirmed'] and execution['cleanup_errors'] == []
        assert execution['process_returncode'] is not None
        raw = (directory / 'events.jsonl').read_bytes()
        events = [json.loads(line) for line in raw.splitlines()]
        assert [event['phase'] for event in events] == (['setup'] if phase == 'call' else ['setup', 'call'])
        report_path = directory / 'report.md'
        report = report_path.read_bytes()
        assert '| incomplete | 1 |' in report.decode('utf-8')
        assert '| not_run | 1 |' in report.decode('utf-8')
        assert '| passed |' not in report.decode('utf-8')
        assert 'launcher_status: timeout' in report.decode('utf-8')
        report_path.write_text('rebuild', encoding='utf-8')
        rebuilt = subprocess.run([sys.executable, 'tests/run.py', '--report-only', str(directory)],
                                 cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15, check=False)
        assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
        assert report_path.read_bytes() == report and (directory / 'events.jsonl').read_bytes() == raw
    finally:
        pid_file = tmp_path / 'descendant.json'
        if pid_file.exists():
            pid = json.loads(pid_file.read_text())
            if _process_running(pid):
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'],
                                   capture_output=True, check=True, timeout=10)
                else:
                    import signal
                    os.kill(pid, signal.SIGKILL)


def test_native_filtered_collection_and_numeric_attempts(tmp_path):
    source = Path(__file__).resolve().parent.parent
    (tmp_path / 'tests').mkdir()
    for name in ['tests/run.py', 'tests/_process_tree.py', 'tests/conftest.py', 'pyproject.toml']:
        shutil.copy2(source / name, tmp_path / name)
    with (tmp_path / 'tests/conftest.py').open('a', encoding='utf-8') as handle:
        handle.write(
            "\ndef pytest_runtest_protocol(item,nextitem):\n"
            "    from _pytest.runner import runtestprotocol\n"
            "    runtestprotocol(item,nextitem=nextitem)\n"
            "    runtestprotocol(item,nextitem=nextitem)\n    return True\n")
    (tmp_path / 'tests/test_minimal.py').write_text(
        "from pathlib import Path\nimport pytest\n"
        "pytestmark=[pytest.mark.unit,pytest.mark.content,pytest.mark.p1,"
        "pytest.mark.cost(cost_class='c1'),pytest.mark.result_type('numeric',detail='metrics')]\n"
        "def test_repeated():\n"
        "    p=Path('attempt.txt')\n    previous=p.exists()\n    p.write_text('attempted')\n"
        "    assert previous,'first attempt rejected'\n"
        "@pytest.mark.external\ndef test_excluded():\n    raise AssertionError('must remain deselected')\n", encoding='utf-8')
    env = dict(os.environ, CUDA_VISIBLE_DEVICES='-1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    child = subprocess.run([sys.executable, 'tests/run.py', '--timeout', '15', '--core', '--', 'tests/test_minimal.py'],
                           cwd=tmp_path, env=env, capture_output=True, text=True, timeout=25, check=False)
    assert child.returncode == 1, child.stdout + child.stderr
    directory = next((tmp_path / '.tmp/test-results').iterdir())
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['collected_test_ids'] == ['tests/test_minimal.py::test_repeated']
    events = [json.loads(line) for line in (directory / 'events.jsonl').read_bytes().splitlines()]
    assert [event['attempt'] for event in events] == [1, 1, 1, 2, 2, 2]
    assert [event['phase'] for event in events] == ['setup', 'call', 'teardown'] * 2
    assert events[4]['status'] == 'measured' and events[4]['verdict'] is None
    report = (directory / 'report.md').read_text(encoding='utf-8')
    assert '| failed | 1 |' in report and '| measured | 1 |' in report
    assert 'first attempt rejected' in report and '| passed |' not in report


@pytest.mark.parametrize('trailing', [b'{"phase":"tear', b'{"reason":"\xf0\x9f'])
def test_report_rebuild_keeps_truncated_final_event_and_known_failure(tmp_path, trailing):
    source = Path(__file__).resolve().parent.parent
    (tmp_path / 'tests').mkdir()
    for name in ['tests/run.py', 'tests/_process_tree.py', 'tests/conftest.py', 'pyproject.toml']:
        shutil.copy2(source / name, tmp_path / name)
    directory = tmp_path / 'saved'
    directory.mkdir()
    (directory / 'manifest.json').write_text(json.dumps({
        'run_id': 'interrupted', 'event_phases': ['setup', 'call', 'teardown'],
        'collected_test_ids': ['test_01', 'test_02']}), encoding='utf-8')
    event = {'test_id': 'test_01', 'attempt': 1, 'phase': 'call', 'status': 'failed',
             'failure': {'longrepr': 'original failure retained'}}
    raw = (json.dumps(event) + '\n').encode() + trailing
    (directory / 'events.jsonl').write_bytes(raw)
    child = subprocess.run([sys.executable, 'tests/run.py', '--report-only', str(directory)],
                           cwd=tmp_path, capture_output=True, text=True, timeout=15, check=False)
    assert child.returncode == 0, child.stdout + child.stderr
    report = (directory / 'report.md').read_text(encoding='utf-8')
    assert '| incomplete | 1 |' in report and '| not_run | 1 |' in report
    assert 'incomplete: `test_01`, attempt 1' in report and 'not_run: `test_02`' in report
    assert 'original failure retained' in report and 'Truncated final event (not counted)' in report
    assert (directory / 'events.jsonl').read_bytes() == raw
    (directory / 'events.jsonl').write_bytes(b'{broken}\n' + raw)
    rejected = subprocess.run([sys.executable, 'tests/run.py', '--report-only', str(directory)],
                              cwd=tmp_path, capture_output=True, text=True, timeout=15, check=False)
    assert rejected.returncode != 0 and 'JSONDecodeError' in rejected.stderr


def test_legacy_report_rebuild_does_not_fabricate_phase_coverage(tmp_path):
    import conftest as records
    (tmp_path / 'manifest.json').write_text(json.dumps({'run_id': 'legacy', 'exit_status': 0}), encoding='utf-8')
    (tmp_path / 'events.jsonl').write_text(json.dumps({
        'test_id': 'legacy', 'attempt': 1, 'status': 'passed'}) + '\n', encoding='utf-8')
    records._write_report(tmp_path, 'legacy', 0)
    report = (tmp_path / 'report.md').read_text(encoding='utf-8')
    assert '| passed | 1 |' in report
    assert 'phase_coverage: legacy (unknown)' in report and 'collection_saved: False' in report

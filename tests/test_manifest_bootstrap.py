"""Native test entry completes when optional local Git metadata is unreadable."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('head,ci_sha', [(b'\xff', None), (b'ref:', None), (b'\xff', 'a' * 40)])
def test_native_test_entry_keeps_optional_corrupt_metadata(tmp_path, head, ci_sha):
    """Copied native tests/run.py → real pytest session → executed fixture and null/explicit SHA manifest."""
    source = Path(__file__).resolve().parent.parent
    (tmp_path / 'tests').mkdir()
    for name in ['tests/run.py', 'tests/conftest.py', 'pyproject.toml']:
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
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
    assert child.returncode == 0, child.stdout
    assert (tmp_path / 'EXECUTED').read_text() == 'ok'
    manifests = list((tmp_path / '.tmp/test-results').glob('*/manifest.json'))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert manifest['git_sha'] == ci_sha
    assert manifest['basetemp'] == str(tmp_path / 'p')
    events = [json.loads(line) for line in (manifests[0].parent / 'events.jsonl').read_text().splitlines()]
    assert len(events) == 1 and events[0]['status'] == 'passed'



@pytest.mark.parametrize('kind', ['valid', 'high', 'low', 'mixed'])
@pytest.mark.parametrize('when', ['call', 'setup'])
def test_native_failure_evidence_preserves_unicode_and_peer(tmp_path, kind, when):
    source = Path(__file__).resolve().parent.parent
    (tmp_path / 'tests').mkdir()
    for name in ['tests/run.py', 'tests/conftest.py', 'pyproject.toml']:
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
    env.update(PYTHONIOENCODING='utf-8:backslashreplace', CUDA_VISIBLE_DEVICES='', MPLBACKEND='Agg')
    child = subprocess.run(
        [sys.executable, 'tests/run.py', '--core', '--', 'tests/test_minimal.py',
         '--basetemp', str(tmp_path / 'p')], cwd=tmp_path, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding='utf-8', errors='backslashreplace', timeout=30)
    assert child.returncode == 1, child.stdout
    assert 'INTERNALERROR' not in child.stdout
    assert (tmp_path / 'EXECUTED_PEER').read_text() == 'ok'
    manifests = list((tmp_path / '.tmp/test-results').glob('*/manifest.json'))
    assert len(manifests) == 1
    raw = (manifests[0].parent / 'events.jsonl').read_bytes()
    events = [json.loads(line) for line in raw.decode('utf-8').splitlines()]
    assert [event['status'] for event in events] == ['failed', 'passed']
    assert events[0]['failure']['when'] == when
    assert text in events[0]['failure']['longrepr']
    assert '正常诊断🙂 '.encode('utf-8') in raw
    report = (manifests[0].parent / 'report.md').read_text(encoding='utf-8')
    assert 'exit_status: 1' in report


@pytest.mark.parametrize('phase', ['teardown', 'setup_skip'])
def test_native_phase_evidence_and_report_rebuild(tmp_path, phase):
    """Real pytest phases survive process exit and reconstruct the same report."""
    source = Path(__file__).resolve().parent.parent
    (tmp_path / 'tests').mkdir()
    for name in ['tests/run.py', 'tests/conftest.py', 'pyproject.toml']:
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
               CUDA_VISIBLE_DEVICES='', MPLBACKEND='Agg', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    child = subprocess.run(
        [sys.executable, 'tests/run.py', '--core', '--', 'tests/test_minimal.py',
         '--basetemp', str(tmp_path / 'p')], cwd=tmp_path, env=env,
        capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert child.returncode == (1 if phase == 'teardown' else 0), child.stdout + child.stderr
    manifests = list((tmp_path / '.tmp/test-results').glob('*/manifest.json'))
    assert len(manifests) == 1
    directory = manifests[0].parent
    raw = (directory / 'events.jsonl').read_bytes()
    events = [json.loads(line) for line in raw.splitlines()]
    guarded = [event for event in events if 'test_01_guarded' in event['test_id']]
    if phase == 'teardown':
        assert [event['status'] for event in guarded] == ['passed', 'failed']
        assert [(event['phase'], event['status']) for event in guarded] == [
            ('call', 'passed'), ('teardown', 'failed')]
        assert 'cleanup refused' in guarded[-1]['failure']['longrepr']
        expected = '| failed | 1 |'
    else:
        assert [(event['phase'], event['status']) for event in guarded] == [('setup', 'skipped')]
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
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    assert report_path.read_bytes() == report
    assert (directory / 'events.jsonl').read_bytes() == raw


def test_default_parallel_sessions_keep_separate_temporary_roots(tmp_path):
    """Two real default invocations cannot clean each other's pytest directory."""
    source = Path(__file__).resolve().parent.parent
    (tmp_path / 'tests').mkdir()
    for name in ['tests/run.py', 'tests/conftest.py', 'pyproject.toml']:
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

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
    results = tmp_path.with_name(tmp_path.name + '-evidence')
    env['RPIPE_TEST_RESULTS_ROOT'] = str(results)
    env.pop('GITHUB_SHA', None)
    if ci_sha is not None:
        env['GITHUB_SHA'] = ci_sha
    child = subprocess.run(
        [sys.executable, 'tests/run.py', '--core', '--', 'tests/test_minimal.py',
         '--basetemp', str(tmp_path / 'p')], cwd=tmp_path, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
    assert child.returncode == 0, child.stdout
    assert (tmp_path / 'EXECUTED').read_text() == 'ok'
    manifests = list(results.glob('*/manifest.json'))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text())['git_sha'] == ci_sha
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
    results = tmp_path.with_name(tmp_path.name + '-evidence')
    env['RPIPE_TEST_RESULTS_ROOT'] = str(results)
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
    manifests = list(results.glob('*/manifest.json'))
    assert len(manifests) == 1
    raw = (manifests[0].parent / 'events.jsonl').read_bytes()
    events = [json.loads(line) for line in raw.decode('utf-8').splitlines()]
    assert [event['status'] for event in events] == ['error' if when == 'setup' else 'failed', 'passed']
    assert events[0]['failure']['when'] == when
    assert text in events[0]['failure']['longrepr']
    assert '正常诊断🙂 '.encode('utf-8') in raw
    report = (manifests[0].parent / 'report.md').read_text(encoding='utf-8')
    assert 'exit_status: 1' in report

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


def _native_protocol(tmp_path, code, *args):
    source = Path(__file__).resolve().parent.parent
    (tmp_path / 'tests').mkdir()
    for name in ['tests/run.py', 'tests/conftest.py', 'pyproject.toml']:
        shutil.copy2(source / name, tmp_path / name)
    (tmp_path / 'tests/test_minimal.py').write_text(
        "from pathlib import Path\nimport pytest\n"
        "pytestmark=[pytest.mark.unit,pytest.mark.content,pytest.mark.p1,"
        "pytest.mark.cost(cost_class='c1'),pytest.mark.result_type('categorical',detail='summary')]\n"
        + code, encoding='utf-8')
    results = tmp_path.with_name(tmp_path.name + '-evidence')
    env = os.environ.copy()
    env.update(PYTHONIOENCODING='utf-8:backslashreplace', CUDA_VISIBLE_DEVICES='', MPLBACKEND='Agg')
    env['RPIPE_TEST_RESULTS_ROOT'] = str(results)
    # A nested test run must own a fresh identity, never append to its caller's run.
    for key in ['RPIPE_TEST_RUN_DIR', 'RPIPE_TEST_ATTEMPT', 'RPIPE_TEST_PLAN']:
        env.pop(key, None)
    child = subprocess.run(
        [sys.executable, 'tests/run.py', *args, '--', 'tests/test_minimal.py',
         '--basetemp', str(tmp_path / 'p')], cwd=tmp_path, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding='utf-8', errors='backslashreplace', timeout=30)
    manifest_path, = results.glob('*/manifest.json')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    events = [json.loads(line) for line in (manifest_path.parent / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
    return child, manifest, events, manifest_path.parent


def test_plan_persists_selection_exclusions_dependencies_and_required_before_execution(tmp_path):
    child, manifest, events, out = _native_protocol(tmp_path,
        "pytestmark = [mark for mark in pytestmark if mark.name != 'cost']\n"
        "@pytest.mark.cost(cost_class='c1')\n"
        "def test_first():\n    pass\n"
        "@pytest.mark.cost(cost_class='c1')\n"
        "@pytest.mark.skipif(True, reason='platform exclusion')\n"
        "def test_platform():\n    pass\n"
        "@pytest.mark.cost(cost_class='c2')\n"
        "def test_filtered():\n    pass\n", '--plan-id', 'protocol_probe', '--cost-class', 'c1')
    assert child.returncode == 0, child.stdout
    assert manifest['plan_id'] == 'protocol_probe'
    assert manifest['required'] == ['tests/test_minimal.py::test_first']
    assert len(manifest['selected']) == 2
    assert manifest['not_selected'][0]['test_id'].endswith('test_filtered')
    assert 'cost' in manifest['not_selected'][0]['reason']
    assert manifest['exclusions'][0]['reason'] == 'platform exclusion'
    assert manifest['dependencies']['pytest'] == pytest.__version__
    assert manifest['plan']['timeout_scope'] == 'pytest_process'
    assert [e['status'] for e in events] == ['passed', 'skipped']
    assert (out / 'report.md').is_file()


def test_explicit_required_missing_from_collection_blocks_even_if_selected_passes(tmp_path):
    child, manifest, events, out = _native_protocol(tmp_path, "def test_first():\n    pass\n",
        '--required', 'tests/test_minimal.py::absent')
    assert child.returncode == 1, child.stdout
    assert manifest['required'] == ['tests/test_minimal.py::absent']
    assert len(events) == 1 and events[0]['status'] == 'passed'
    assert json.loads((out / 'summary.json').read_text())['evidence_complete'] is False


def test_batch_retry_preserves_failed_attempt_and_marks_flaky(tmp_path):
    child, manifest, events, out = _native_protocol(tmp_path,
        "def test_flaky():\n"
        "    flag = Path('seen')\n"
        "    seen = flag.exists()\n"
        "    flag.write_text('seen')\n"
        "    assert seen\n", '--retries', '1')
    assert child.returncode == 0, child.stdout
    assert [(e['attempt'], e['status']) for e in events] == [(1, 'failed'), (2, 'passed')]
    assert manifest['plan']['retry_scope'] == 'batch'
    assert 'flaky' in (out / 'report.md').read_text(encoding='utf-8')


def test_owned_batch_timeout_keeps_done_case_and_records_active_timeout(tmp_path):
    child, manifest, events, out = _native_protocol(tmp_path,
        "def test_01_done():\n    pass\n"
        "def test_02_wait():\n    import time\n    time.sleep(15)\n", '--timeout-seconds', '8')
    assert child.returncode == 1, child.stdout
    assert [(e['test_id'].split('::')[-1], e['status']) for e in events] == [
        ('test_01_done', 'passed'), ('test_02_wait', 'timeout')]
    assert events[1]['verdict'] is None
    assert manifest['attempts'][-1]['status'] == 'timeout'
    rebuilt = subprocess.run([sys.executable, 'tests/run.py', '--report', str(out)],
                             cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert rebuilt.returncode == 1
    assert 'timeout' in (out / 'report.md').read_text(encoding='utf-8')


def test_pytest_interrupt_keeps_completed_evidence_and_lists_unexecuted_contract(tmp_path):
    child, manifest, events, out = _native_protocol(tmp_path,
        "def test_01_done():\n    pass\n"
        "def test_02_interrupt():\n    raise KeyboardInterrupt()\n"
        "def test_03_unexecuted():\n    Path('MUST_NOT_EXIST').write_text('bad')\n")
    assert child.returncode == 2, child.stdout
    assert events[0]['status'] == 'passed' and events[1]['status'] == 'error'
    assert not (tmp_path / 'MUST_NOT_EXIST').exists()
    summary = json.loads((out / 'summary.json').read_text())
    assert summary['missing_required'] == ['tests/test_minimal.py::test_03_unexecuted']
    assert manifest['not_executed'][0]['test_id'].endswith('test_03_unexecuted')
    assert manifest['attempts'][0]['status'] == 'interrupted'
    assert summary['blocking'] is True


@pytest.mark.parametrize('mode', ['dynamic_skip', 'xfail', 'xpass', 'filtered_required', 'gpu'])
def test_unfulfilled_required_contracts_block_without_running_unavailable_resources(tmp_path, mode):
    args = []
    if mode == 'filtered_required':
        code = "def test_first():\n    pass\n@pytest.mark.p2\ndef test_filtered():\n    pass\n"
        code = "pytestmark = [m for m in pytestmark if m.name != 'p1']\n" + code.replace(
            'def test_first()', '@pytest.mark.p1\ndef test_first()')
        args = ['--priority', 'p1', '--required', 'tests/test_minimal.py::test_filtered']
    elif mode == 'gpu':
        code = ("pytestmark = [m for m in pytestmark if m.name != 'cost']\n"
                "@pytest.mark.cost(cost_class='c3',gpu_count=1)\n"
                "def test_first():\n    Path('MUST_NOT_EXIST').write_text('bad')\n")
    elif mode == 'dynamic_skip':
        code = "def test_first():\n    pytest.skip('dependency unavailable at runtime')\n"
    else:
        code = ("@pytest.mark.xfail(reason='known issue')\ndef test_first():\n    "
                + ("assert False\n" if mode == 'xfail' else "pass\n"))
    child, manifest, events, out = _native_protocol(tmp_path, code, *args)
    assert child.returncode == 1, child.stdout
    assert not (tmp_path / 'MUST_NOT_EXIST').exists()
    assert json.loads((out / 'summary.json').read_text())['blocking'] is True
    if mode in {'xfail', 'xpass'}:
        assert events[0]['status'] == mode and events[0]['verdict'] is None


def test_native_numeric_measurement_and_collection_error_never_become_quality_pass(tmp_path):
    child, _, events, out = _native_protocol(tmp_path,
        "pytestmark = [m for m in pytestmark if m.name != 'result_type'] + [pytest.mark.result_type('numeric',detail='metrics')]\n"
        "def test_01_measure(record_property):\n"
        "    record_property('metrics', [{'name':'observed','value':7,'unit':'count'}])\n"
        "def test_02_collection_failure(record_property):\n"
        "    record_property('metrics', [{'name':'partial','value':2,'unit':'count'}])\n"
        "    raise OSError('controlled collection failure')\n")
    assert child.returncode == 1, child.stdout
    assert [(event['status'], event['verdict']) for event in events] == [('measured', None), ('error', None)]
    assert events[1]['metrics'][0]['value'] == 2 and events[1]['exception_type'] == 'OSError'
    summary = json.loads((out / 'summary.json').read_text())
    assert summary['measurement_completed_count'] == 1 and summary['quality_pass_count'] == 0


def test_native_threshold_failure_keeps_numerical_evidence(tmp_path):
    child, _, events, _ = _native_protocol(tmp_path,
        "def test_threshold(record_property):\n"
        "    record_property('metrics', [{'name':'observed','value':7,'unit':'count',"
        "'threshold':{'operator':'<=','value':5,'source':'controlled fixture contract'}}])\n"
        "    assert 7 <= 5\n")
    assert child.returncode == 1, child.stdout
    assert events[0]['status'] == 'failed' and events[0]['verdict'] == 'fail'
    assert events[0]['metrics'][0]['value'] == 7 and events[0]['exception_type'] == 'AssertionError'


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
    env['PYTHONIOENCODING'] = 'utf-8:backslashreplace'
    env['RPIPE_TEST_RESULTS_ROOT'] = str(results)
    env.pop('GITHUB_SHA', None)
    if ci_sha is not None:
        env['GITHUB_SHA'] = ci_sha
    child = subprocess.run(
        [sys.executable, 'tests/run.py', '--core', '--', 'tests/test_minimal.py',
         '--basetemp', str(tmp_path / 'p')], cwd=tmp_path, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding='utf-8', errors='backslashreplace', timeout=30)
    assert child.returncode == 0, child.stdout
    assert (tmp_path / 'EXECUTED').read_text() == 'ok'
    manifests = list(results.glob('*/manifest.json'))
    assert len(manifests) == 1
    assert json.loads(manifests[0].read_text(encoding='utf-8'))['git_sha'] == ci_sha
    events = [json.loads(line) for line in (manifests[0].parent / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
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

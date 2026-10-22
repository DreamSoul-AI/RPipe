"""Recorded event time identifies the actual reported phase start."""
import json
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

_spec = importlib.util.spec_from_file_location('rpipe_test_recorder', Path(__file__).with_name('conftest.py'))
records = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(records)
pytest_runtest_makereport = records.pytest_runtest_makereport

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def _record(item, report):
    hook = pytest_runtest_makereport(item, None)
    next(hook)
    with pytest.raises(StopIteration):
        hook.send(SimpleNamespace(get_result=lambda: report))


def _finish(item, start):
    _record(item, SimpleNamespace(when='teardown', failed=False, passed=True,
                                 skipped=False, duration=0, start=start))


@pytest.mark.parametrize('when', ['call', 'setup'])
def test_event_started_at_is_report_start_not_serialization_time(tmp_path, request, when):
    config = SimpleNamespace(_rpipe_results_dir=tmp_path, _rpipe_run_id='event-clock')
    item = SimpleNamespace(config=config, nodeid='synthetic::phase', location=('synthetic.py', 0, 'phase'),
                           iter_markers=request.node.iter_markers)
    report = SimpleNamespace(when=when, failed=when == 'setup', passed=when == 'call',
                             skipped=False, duration=1.25, start=946684800.5,
                             longrepr='fixture setup failure')
    _record(item, report)
    _finish(item, report.start)
    event = json.loads((tmp_path / 'events.jsonl').read_text())
    assert event['started_at'] == datetime.fromtimestamp(report.start, timezone.utc).isoformat()
    assert event['duration_ms'] == event['cost']['actual_wall_time_ms'] == 1250
    assert event['status'] == ('error' if when == 'setup' else 'passed')



def _diagnostic(kind):
    return '正常诊断🙂 ' + {'valid': 'normal', 'high': chr(0xD800),
                          'low': chr(0xDCFF), 'mixed': chr(0xD800) + '/' + chr(0xDCFF)}[kind]


@pytest.mark.parametrize('kind', ['valid', 'high', 'low', 'mixed'])
@pytest.mark.parametrize('when', ['call', 'setup'])
def test_failure_event_preserves_unicode_and_verdict(tmp_path, request, kind, when):
    text = _diagnostic(kind)
    config = SimpleNamespace(_rpipe_results_dir=tmp_path, _rpipe_run_id='unicode-event')
    item = SimpleNamespace(config=config, nodeid='synthetic::failure',
                           location=('synthetic.py', 0, 'failure'),
                           iter_markers=request.node.iter_markers)
    report = SimpleNamespace(when=when, failed=True, passed=False, skipped=False,
                             duration=1.25, start=946684800.5, longrepr=text)
    _record(item, report)
    _finish(item, report.start)
    raw = (tmp_path / 'events.jsonl').read_bytes()
    event = json.loads(raw)
    assert event['failure'] == {'longrepr': text, 'when': when}
    assert (event['status'], event['verdict'], event['category']) == (
        ('error', None, None) if when == 'setup' else ('failed', 'fail', 'fail'))
    assert event['started_at'] == datetime.fromtimestamp(report.start, timezone.utc).isoformat()
    assert event['duration_ms'] == event['cost']['actual_wall_time_ms'] == 1250
    assert '正常诊断🙂 '.encode('utf-8') in raw


@pytest.mark.parametrize('kind', ['valid', 'high', 'low', 'mixed'])
def test_manifest_preserves_unicode_arguments(tmp_path, monkeypatch, kind):
    text = _diagnostic(kind)
    monkeypatch.setattr(records, 'RESULTS_ROOT', tmp_path)
    monkeypatch.setenv('GITHUB_SHA', 'a' * 40)
    config = SimpleNamespace(invocation_params=SimpleNamespace(args=['pytest', text]),
                             option=SimpleNamespace(markexpr=text, cost_class=[]))
    records.pytest_configure(config)
    raw = (config._rpipe_results_dir / 'manifest.json').read_bytes()
    manifest = json.loads(raw)
    assert manifest['command'] == ['pytest', text]
    assert manifest['markexpr'] == text
    assert manifest['git_sha'] == 'a' * 40
    assert '正常诊断🙂 '.encode('utf-8') in raw


def test_teardown_error_preserves_first_failure_and_total_duration(tmp_path, request):
    item = SimpleNamespace(config=SimpleNamespace(_rpipe_results_dir=tmp_path, _rpipe_run_id='phases'),
                           nodeid='synthetic::cleanup', location=('synthetic.py', 0, 'cleanup'),
                           iter_markers=request.node.iter_markers)
    for when, duration, failed, message in [
        ('setup', .1, False, ''), ('call', .2, True, 'primary assertion'),
        ('teardown', .3, True, 'secondary cleanup')]:
        _record(item, SimpleNamespace(when=when, duration=duration, failed=failed,
                                      passed=not failed, skipped=False, start=0, longrepr=message))
    event = json.loads((tmp_path / 'events.jsonl').read_text())
    assert (event['status'], event['verdict']) == ('error', None)
    assert event['failure'] == {'longrepr': 'primary assertion', 'when': 'call'}
    assert event['cleanup_failures'] == [{'longrepr': 'secondary cleanup', 'when': 'teardown'}]
    assert event['duration_ms'] == 600
    assert set(event['phases']) == {'setup', 'call', 'teardown'}


@pytest.mark.parametrize('metrics,status', [
    ([{'name': 'value', 'value': 2.0, 'unit': 'count'}], 'measured'),
    ([], 'error'),
    ([{'name': 'value', 'value': float('nan'), 'unit': 'count'}], 'error'),
])
def test_numeric_record_requires_valid_measurement_and_never_quality_pass(tmp_path, request, metrics, status):
    marks = [mark for mark in request.node.iter_markers() if mark.name != 'result_type']
    marks.append(pytest.mark.result_type('numeric', detail='metrics').mark)
    item = SimpleNamespace(config=SimpleNamespace(_rpipe_results_dir=tmp_path, _rpipe_run_id='numeric'),
                           nodeid='synthetic::measurement', location=('synthetic.py', 0, 'measurement'),
                           iter_markers=lambda name=None: (mark for mark in marks if name is None or mark.name == name),
                           user_properties=[('metrics', metrics)])
    _record(item, SimpleNamespace(when='call', duration=.1, failed=False,
                                  passed=True, skipped=False, start=0))
    _finish(item, 0)
    event = json.loads((tmp_path / 'events.jsonl').read_text())
    assert event['status'] == status
    assert event['verdict'] is None
    assert event['category'] is None
    assert len(event['metrics']) == len(metrics)


def test_report_rebuilds_from_disk_and_lists_missing_results(tmp_path):
    write_report = records.write_report

    (tmp_path / 'manifest.json').write_text(json.dumps({'run_id': 'partial', 'selected': ['done', 'missing']}))
    (tmp_path / 'events.jsonl').write_text(json.dumps({'test_id': 'done', 'status': 'measured'}) + '\n')
    write_report(tmp_path, 2)
    report = (tmp_path / 'report.md').read_text(encoding='utf-8')
    assert '| measured | 1 |' in report
    assert '未取得完整结果：1' in report and '`missing`' in report

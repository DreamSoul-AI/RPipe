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


def test_disk_report_counts_attempts_once_per_case_and_blocks_required_skip(tmp_path):
    (tmp_path / 'manifest.json').write_text(json.dumps({
        'run_id': 'retry', 'plan_id': 'core', 'selected': ['flaky', 'skip'],
        'required': ['flaky', 'skip', 'not_collected'],
    }))
    events = [
        {'test_id': 'flaky', 'attempt': 1, 'status': 'failed', 'duration_ms': 4},
        {'test_id': 'flaky', 'attempt': 2, 'status': 'passed', 'duration_ms': 6},
        {'test_id': 'skip', 'attempt': 2, 'status': 'skipped', 'duration_ms': 1},
    ]
    (tmp_path / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
    summary = records.write_report(tmp_path, 0)
    assert summary['case_count'] == 2 and summary['attempt_count'] == 3
    assert summary['flaky'] == ['flaky']
    assert summary['missing_required'] == ['not_collected']
    assert summary['blocking'] is True and summary['actual_wall_time_ms'] == 11
    assert summary['required_incomplete'] == ['not_collected', 'skip']
    report = (tmp_path / 'report.md').read_text(encoding='utf-8')
    assert 'plan_id: `core`' in report and 'flaky' in report


def test_disk_report_survives_interrupted_last_json_line_without_losing_done_case(tmp_path):
    (tmp_path / 'manifest.json').write_text(json.dumps({
        'run_id': 'partial', 'selected': ['done', 'active'], 'required': ['done', 'active'],
    }))
    (tmp_path / 'events.jsonl').write_text(
        json.dumps({'test_id': 'done', 'attempt': 1, 'status': 'passed'}) + '\n{"test_id":')
    summary = records.write_report(tmp_path, 2)
    assert summary['case_count'] == 1
    assert summary['missing_required'] == ['active']
    assert summary['evidence_errors'] and summary['blocking'] is True


@pytest.mark.parametrize('field,value', [('run_id', 'other-run'), ('plan_id', 'other-plan'),
                                       ('environment_id', 'other-env'), ('attempt', 99),
                                       ('run_id', None), ('plan_id', None),
                                       ('environment_id', None), ('attempt', None), ('test_id', 'unselected')])
def test_report_rejects_foreign_execution_evidence_without_losing_healthy_peer(tmp_path, field, value):
    manifest = {'run_id': 'current', 'plan_id': 'plan', 'environment_id': 'cpu',
                'selected': ['healthy', 'foreign'], 'required': ['healthy', 'foreign'],
                'collection_complete': True, 'exit_status': 0,
                'attempts': [{'attempt': 1, 'status': 'completed', 'exit_status': 0}]}
    event = {'run_id': 'current', 'plan_id': 'plan', 'environment_id': 'cpu',
             'attempt': 1, 'test_id': 'healthy', 'status': 'passed', 'duration_ms': 1}
    foreign = dict(event, test_id='foreign')
    foreign[field] = value
    if value is None:
        foreign.pop(field)
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    events = (tmp_path / 'events.jsonl')
    events.write_text(json.dumps(event) + '\n' + json.dumps(foreign) + '\n', encoding='utf-8')
    before = events.read_bytes()
    summary = records.write_report(tmp_path, 0)
    assert summary['counts'] == {'passed': 1}
    assert summary['missing_required'] == ['foreign']
    assert summary['blocking'] and not summary['plan_complete'] and not summary['evidence_complete']
    assert summary['evidence_errors'] and field in ' '.join(summary['evidence_errors'])
    assert events.read_bytes() == before


def test_report_accepts_declared_retry_history_with_matching_context(tmp_path):
    manifest = {'run_id': 'current', 'plan_id': 'plan', 'environment_id': 'cpu',
                'selected': ['flaky'], 'required': ['flaky'], 'exit_status': 0,
                'attempts': [{'attempt': 1, 'status': 'failed', 'exit_status': 1},
                             {'attempt': 2, 'status': 'completed', 'exit_status': 0}]}
    events = [dict(run_id='current', plan_id='plan', environment_id='cpu', test_id='flaky',
                   attempt=attempt, status=status, duration_ms=1)
              for attempt, status in [(1, 'failed'), (2, 'passed')]]
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (tmp_path / 'events.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')
    summary = records.write_report(tmp_path)
    assert summary['attempt_count'] == 2 and summary['counts'] == {'passed': 1}
    assert summary['flaky'] == ['flaky']
    assert not summary['blocking'] and summary['plan_complete'] and summary['evidence_complete']


def test_manifest_replace_retries_only_transient_windows_sharing_error(tmp_path, monkeypatch):
    path = tmp_path / 'manifest.json'
    path.write_text('{"old":true}')
    native, attempts = Path.replace, []
    def transient(staging, target):
        attempts.append(target)
        if len(attempts) < 3:
            error = PermissionError('Windows transient sharing violation')
            error.winerror = 32
            raise error
        return native(staging, target)
    monkeypatch.setattr(Path, 'replace', transient)
    records.save_json(path, {'new': True})
    assert len(attempts) == 3 and json.loads(path.read_text()) == {'new': True}


def test_event_append_after_interruption_preserves_complete_events_and_damaged_tail(tmp_path):
    path = tmp_path / 'events.jsonl'
    first = {'test_id': 'done', 'attempt': 1, 'status': 'passed'}
    second = {'test_id': 'active', 'attempt': 1, 'status': 'timeout'}
    path.write_bytes((json.dumps(first) + '\n{"cut":').encode() + b'\xe4')
    records.append_event(path, second)
    events, errors = records._entry.read_events(tmp_path)
    assert events == [first, second] and len(errors) == 1
    assert b'{"cut":\xe4' in path.read_bytes()


@pytest.mark.parametrize('outcome,wasxfail,status', [
    ('skipped', False, 'skipped'), ('skipped', True, 'xfail'), ('passed', True, 'xpass'),
])
def test_skip_xfail_xpass_are_separate_states_without_normal_quality_verdict(tmp_path, request, outcome, wasxfail, status):
    item = SimpleNamespace(config=SimpleNamespace(_rpipe_results_dir=tmp_path, _rpipe_run_id='states'),
                           nodeid='synthetic::state', location=('synthetic.py', 0, 'state'),
                           iter_markers=request.node.iter_markers)
    report = SimpleNamespace(when='call', duration=.1, failed=False, passed=outcome == 'passed',
                             skipped=outcome == 'skipped', start=0, wasxfail=wasxfail, longrepr='expected condition')
    _record(item, report)
    _finish(item, 0)
    event = json.loads((tmp_path / 'events.jsonl').read_text())
    assert event['status'] == status and event['verdict'] is None


@pytest.mark.parametrize('numeric', [False, True])
def test_failed_collection_or_threshold_preserves_the_measurement(tmp_path, request, numeric):
    marks = [mark for mark in request.node.iter_markers() if mark.name != 'result_type']
    marks.append(pytest.mark.result_type('numeric' if numeric else 'categorical', detail='metrics').mark)
    metrics = [{'name': 'observed', 'value': 7, 'unit': 'count'}]
    item = SimpleNamespace(config=SimpleNamespace(_rpipe_results_dir=tmp_path, _rpipe_run_id='failed-value'),
                           nodeid='synthetic::value', location=('synthetic.py', 0, 'value'),
                           iter_markers=lambda name=None: (mark for mark in marks if name is None or mark.name == name),
                           user_properties=[('metrics', metrics)])
    _record(item, SimpleNamespace(when='call', duration=.1, failed=True, passed=False, skipped=False,
                                  start=0, longrepr='collection failed' if numeric else '7 > threshold 5'))
    _finish(item, 0)
    event = json.loads((tmp_path / 'events.jsonl').read_text())
    assert event['metrics'] == metrics
    assert (event['status'], event['verdict']) == (('error', None) if numeric else ('failed', 'fail'))

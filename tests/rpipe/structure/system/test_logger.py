"""Bounded diagnostic records cannot change scientific success or its first error."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import pytest

from rpipe.structure.algorithm.tracker import AlgorithmTracker
from rpipe.structure.system.logger import Logger

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_system,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def records(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def test_jsonl_identity_and_all_five_levels(tmp_path, capsys):
    logger = Logger(tmp_path / 'runs' / 'abc123' / 'assets', logger='rpipe.test',
                    runtime_id='runtime-a', request_id='request-a', attempt_id='attempt-a',
                    workflow_id='study-a', debug=True)
    logger.bind(phase='execute')
    for level in ('debug', 'info', 'warning', 'error', 'critical'):
        getattr(logger, level)('known event', event='test.observed')
    rows = records(logger.path)
    assert [row['level'] for row in rows] == ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    assert logger.path.name == 'events.jsonl'
    assert logger.path.read_text(encoding='utf-8') == capsys.readouterr().out
    for row in rows:
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z', row['timestamp'])
        assert row['pid'] == os.getpid() and row['process_name']
        assert row['app_name'] == 'rpipe' and row['app_version']
        assert row['logger'] == 'rpipe.test' and row['task_id'] == 'abc123'
        assert row['runtime_id'] == 'runtime-a' and row['request_id'] == 'request-a'
        assert row['attempt_id'] == 'attempt-a' and row['workflow_id'] == 'study-a'
        assert row['phase'] == 'execute'


def test_default_debug_and_unknown_context_are_omitted(tmp_path):
    logger = Logger(tmp_path, request_id=None)
    logger.debug('not enabled')
    logger.info('static context')
    row, = records(logger.path)
    assert 'request_id' not in row and 'task_id' not in row and row['level'] == 'INFO'


def test_explicit_checkpoint_event_names_are_preserved_without_alias_translation(tmp_path):
    logger = Logger(tmp_path, stdout=False)
    for event in ('checkpoint.saved', 'checkpoint.restored', 'ckpt', 'resume'):
        logger.info('checkpoint observation', event=event)
    assert [row['event'] for row in records(logger.path)] == [
        'checkpoint.saved', 'checkpoint.restored',
        'logging.unstructured_record', 'logging.unstructured_record']


def test_privacy_and_controls_are_handled_before_either_sink(tmp_path, capsys):
    logger = Logger(tmp_path)
    logger.info('token=TEST_SECRET path=C:\\private\\person.txt email=person@example.test\n'
                '\x1b[31m\r\u2028phase=execute', event='test.observed',
                token='OTHER_SECRET', request_id='x' * 1000)
    text = logger.path.read_text(encoding='utf-8')
    assert text == capsys.readouterr().out and len(text.splitlines()) == 1
    for secret in ('TEST_SECRET', 'OTHER_SECRET', 'person.txt', 'person@example.test'):
        assert secret not in text
    assert '\x1b' not in text and '\u2028' not in text
    row, = records(logger.path)
    assert 'token' not in row and 'request_id' not in row
    assert 'request_id' in row['truncated_fields']


@pytest.mark.parametrize('control', ['\n', '\r\n', '\x1b', '\x85', '\u2028', '\u202e'])
def test_external_controls_cannot_make_or_reorder_a_display_record(tmp_path, capsys, control):
    logger = Logger(tmp_path)
    logger.info('first' + control + 'second', event='test.observed')
    text = logger.path.read_text(encoding='utf-8')
    assert len(text.splitlines()) == 1
    assert control not in text[:-1] and text == capsys.readouterr().out
    assert 'first' in records(logger.path)[0]['message'] and 'second' in records(logger.path)[0]['message']


def test_exception_retains_location_and_chain_without_private_text(tmp_path, capsys):
    logger = Logger(tmp_path)
    def inner():
        raise ValueError('token=TEST_SECRET user input PRIVATE_BODY C:\\private\\person.txt')
    try:
        try:
            inner()
        except ValueError as error:
            raise RuntimeError('password=OTHER_SECRET') from error
    except RuntimeError as error:
        logger.exception('scientific worker failed', error)
    text = logger.path.read_text(encoding='utf-8')
    assert text == capsys.readouterr().out
    row, = records(logger.path)
    assert row['level'] == 'ERROR' and row['exception_type'] == 'RuntimeError'
    assert 'inner' in row['traceback'] and 'ValueError' in text
    for secret in ('TEST_SECRET', 'OTHER_SECRET', 'PRIVATE_BODY', 'person.txt', str(tmp_path)):
        assert secret not in text


@pytest.mark.parametrize('failure', ['closed', 'pipe', 'programming'])
def test_stdout_failure_keeps_file_and_reports_gap(tmp_path, failure):
    logger = Logger(tmp_path)
    if failure == 'closed':
        sink = io.StringIO()
        sink.close()
    elif failure == 'pipe':
        reader, writer = os.pipe()
        os.close(reader)
        sink = os.fdopen(writer, 'w', encoding='utf-8', buffering=1)
    else:
        class Sink:
            def write(self, value):
                raise RuntimeError('PRIVATE_STDOUT_FAILURE')
            def flush(self):
                pass
        sink = Sink()
    with contextlib.redirect_stdout(sink):
        logger.info('science still running', event='task.started')
    if failure == 'pipe':
        with contextlib.suppress(OSError):
            sink.close()
        sink.buffer.close()
    rows = records(logger.path)
    assert rows[0]['event'] == 'task.started'
    fault = next(row for row in rows if row['event'] == 'logging.sink_failed')
    assert fault['sink'] == 'stdout' and fault['drop_reason'] == 'delivery_unconfirmed'
    assert 'dropped_count' not in fault
    assert not logger.diagnostics_complete and 'PRIVATE_STDOUT_FAILURE' not in str(rows)


@pytest.mark.parametrize('stage', ['open', 'write', 'flush', 'close'])
def test_file_failure_keeps_stdout_and_scientific_error(
        tmp_path, monkeypatch, capsys, stream_close_fault, stage):
    logger = Logger(tmp_path)
    before = logger.path.read_bytes()
    if stage == 'open':
        original = Path.open
        def opening(path, mode='r', *args, **kwargs):
            if path == logger.path and mode == 'ab':
                raise PermissionError('PRIVATE_FILE_FAILURE')
            return original(path, mode, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', opening)
    else:
        stream_close_fault(lambda path, mode: path == logger.path and mode == 'ab',
                           None if stage == 'close' else OSError('PRIVATE_FILE_FAILURE'),
                           OSError('PRIVATE_FILE_FAILURE') if stage == 'close' else None,
                           phase='write' if stage == 'close' else stage)
    logger.exception('scientific worker failed', ValueError('PRIVATE_PRIMARY'))
    out = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert out[0]['exception_type'] == 'ValueError'
    assert any(row['event'] == 'logging.sink_failed' and row['sink'] == 'file' for row in out)
    assert not logger.diagnostics_complete
    assert 'PRIVATE_FILE_FAILURE' not in str(out) and 'PRIVATE_PRIMARY' not in str(out)
    assert logger.path.read_bytes().startswith(before)


def test_records_files_and_first_failure_remain_bounded(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(Logger, 'MAX_FILE_BYTES', 20_000)
    logger = Logger(tmp_path)
    logger.exception('first scientific failure', ValueError('private arbitrary exception'))
    for _ in range(100):
        logger.info('界' * 10_000, event='test.observed')
    logger.info('task finished', event='task.succeeded')
    files = list(logger.path.parent.glob('events*.jsonl'))
    assert len(files) <= 5
    assert all(path.stat().st_size <= logger.MAX_FILE_BYTES for path in files)
    assert all(len(line) + 1 <= logger.MAX_RECORD_BYTES for path in files
               for line in path.read_bytes().splitlines())
    all_rows = [row for path in files for row in records(path)]
    assert any(row['event'] == 'logging.records_dropped' for row in all_rows)
    assert any(row['event'] == 'task.succeeded' for row in all_rows)
    assert any(row.get('exception_type') == 'ValueError' for row in all_rows)
    assert any('message' in row.get('truncated_fields', []) for row in all_rows)
    capsys.readouterr()


def test_retention_only_removes_owned_logs(tmp_path):
    logger = Logger(tmp_path)
    logger.info('old event')
    peer = logger.path.parent / 'user-data.jsonl'
    peer.write_bytes(b'private user input')
    old = time.time() - logger.RETENTION_SECONDS - 1
    os.utime(logger.path, (old, old))
    os.utime(peer, (old, old))
    logger.close()
    assert not logger.path.exists() and peer.read_bytes() == b'private user input'


def test_report_keeps_diagnostic_metrics_without_arbitrary_extra(tmp_path, capsys):
    tracker = AlgorithmTracker(tmp_path)
    tracker.append('test', n=10, values={'Loss': 0.5, 'Accuracy': 0.8})
    tracker.save('test')
    tracker.reset('test')
    logger = Logger(tmp_path)
    logger.report(tracker, 'test', {'epoch': 1, 'lr': 0.1, 'step': 2,
                                  'note': 'PRIVATE_BODY', 'elapsed': '0:00:01'})
    row, = records(logger.path)
    assert '[split] test [metric] Accuracy=0.8000 Loss=0.5000' in row['message']
    assert 'lr=0.1 step=2' in row['message'] and 'PRIVATE_BODY' not in str(row)
    assert row['event'] == 'metrics.reported'
    assert not hasattr(logger, 'state_dict') and not hasattr(logger, 'load_state_dict')
    assert capsys.readouterr().out


def test_busy_cross_process_log_lock_never_waits_for_scientific_owner(tmp_path):
    from rpipe.structure.artifact.io import cache_lock
    logger = Logger(tmp_path)
    before = logger.path.read_bytes()
    script = ('import sys; from rpipe.structure.system.logger import Logger; '
              'Logger(sys.argv[1]).info("scientific work continues",event="task.started")')
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[4] / 'src')}
    with cache_lock(logger.path.parent, timeout=0):
        child = subprocess.run([sys.executable, '-B', '-c', script, str(tmp_path)],
                               env=env, capture_output=True, text=True, timeout=10)
    assert child.returncode == 0, child.stderr
    rows = [json.loads(line) for line in child.stdout.splitlines()]
    assert any(row['event'] == 'logging.sink_failed' and row['sink'] == 'file' for row in rows)
    assert logger.path.read_bytes() == before
    logger.info('owner released')
    assert records(logger.path)[-1]['message'] == 'owner released'


def test_explicit_factory_logger_injection_does_not_create_another_sink(tmp_path, monkeypatch):
    import rpipe.structure.system.factory as factory
    from rpipe.structure.control import SystemConfig
    logger = Logger(tmp_path)
    def unexpected(*args, **kwargs):
        raise AssertionError('must reuse the execution logger')
    monkeypatch.setattr(factory, 'Logger', unexpected)
    assert factory.build_system(SystemConfig.from_mapping({'device': 'cpu'}), tmp_path,
                                       logger=logger).logger is logger


def test_sink_recovery_reports_gap_once_without_forgetting_incomplete_evidence(tmp_path, monkeypatch, capsys):
    logger = Logger(tmp_path)
    write = logger._write_file
    monkeypatch.setattr(logger, '_write_file', lambda record: (_ for _ in ()).throw(OSError('private')))
    logger.info('first delivery')
    monkeypatch.setattr(logger, '_write_file', write)
    logger.info('second delivery')
    logger.info('third delivery')
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert sum(row['event'] == 'logging.sink_failed' for row in rows) == 1
    assert sum(row['event'] == 'logging.recovered' for row in rows) == 1
    assert not logger.diagnostics_complete


def test_interrupted_record_does_not_hide_the_next_complete_event(tmp_path):
    logger = Logger(tmp_path)
    fragment = b'{"event":"test.interrupted","message":"incomplete'
    logger.path.write_bytes(fragment)
    peer = logger.path.parent / 'business-input.json'
    peer.write_bytes(b'original input')
    logger.error('current failure', event='task.failed')
    rows = records(logger.path)
    assert any(row['event'] == 'task.failed' for row in rows)
    assert any(row['event'] == 'logging.unstructured_record' for row in rows)
    assert logger.path.with_name('events.1.jsonl').read_bytes() == fragment
    assert peer.read_bytes() == b'original input' and not logger.diagnostics_complete


def test_retention_failure_is_reported_to_the_healthy_sink(tmp_path, monkeypatch, capsys):
    logger = Logger(tmp_path)
    monkeypatch.setattr(logger, '_expire', lambda: (_ for _ in ()).throw(PermissionError('PRIVATE_CLEANUP')))
    logger.close()
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any(row['event'] == 'logging.sink_failed' and row['operation'] == 'retention_cleanup' for row in rows)
    assert not logger.diagnostics_complete and 'PRIVATE_CLEANUP' not in str(rows)

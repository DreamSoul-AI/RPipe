import re
import contextlib
import io
import os

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.content,
    pytest.mark.p1,
    pytest.mark.structure_layer,
    pytest.mark.module_system,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]

from pathlib import Path

from rpipe.payloads.tracker import AlgorithmTracker
from rpipe.payloads.logger import Logger

_HEADER = re.compile(
    r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2} '
    r'(INFO |WARN |ERROR) '
)


def test_logger_writes_timestamp_level_and_run_id(tmp_path: Path, capsys):
    assets = tmp_path / 'runs' / 'abc123' / 'assets'
    logger = Logger(assets)
    logger.info('start phases=prepare')
    captured = capsys.readouterr().out
    assert _HEADER.match(captured)
    assert ' INFO  abc123 [flow] start phases=prepare' in captured
    text = (assets / 'logs' / 'run.log').read_text(encoding='utf-8')
    assert text == captured


def test_logger_report_uses_metric_tags(tmp_path: Path, capsys):
    tracker = AlgorithmTracker(tmp_path)
    tracker.append('train', n=1, values={'Loss': 0.5, 'Accuracy': 0.25})
    logger = Logger(tmp_path)
    logger.report(tracker, 'train', extra={'epoch': 1, 'elapsed': '0:00:01', 'eta': '0:00:09'})
    text = (tmp_path / 'logs' / 'run.log').read_text(encoding='utf-8')
    assert _HEADER.match(text)
    assert ' - [epoch] 1 [split] train [metric] Accuracy=0.2500 Loss=0.5000 [time] elapsed=0:00:01 eta=0:00:09' in text
    assert capsys.readouterr().out == text


def test_logger_exception_writes_traceback_on_each_line(tmp_path: Path, capsys):
    assets = tmp_path / 'runs' / 'deadbeef' / 'assets'
    logger = Logger(assets)

    def inner():
        raise ValueError('nope')

    try:
        inner()
    except ValueError as exc:
        logger.exception('phase=execute status=failed', exc)

    text = (assets / 'logs' / 'run.log').read_text(encoding='utf-8')
    out = capsys.readouterr().out
    for blob in (text, out):
        assert ' ERROR deadbeef [error] phase=execute status=failed ValueError: nope' in blob
        assert ' ERROR deadbeef [error] Traceback (most recent call last):' in blob
        assert 'ValueError: nope' in blob
        assert 'inner' in blob


def test_logger_report_after_save_reset_uses_segment(tmp_path: Path, capsys):
    tracker = AlgorithmTracker(tmp_path)
    tracker.append('test', n=10, values={'Loss': 0.5, 'Accuracy': 0.8})
    tracker.save('test')
    tracker.reset('test')
    Logger(tmp_path).report(tracker, 'test')
    text = capsys.readouterr().out
    assert '[split] test [metric] Accuracy=0.8000 Loss=0.5000' in text


def test_logger_constructor_preserves_evidence_arriving_before_open(tmp_path, monkeypatch):
    from rpipe.structure import artifacts as kinds

    target = tmp_path / kinds.RUN_LOG
    native = Path.open
    arrivals = []

    def arriving(path, mode='r', *args, **kwargs):
        if path == target and mode in ('w', 'a') and not arrivals:
            arrivals.append(True)
            with native(path, 'wb') as stream:
                stream.write(b'prior error evidence\n')
        return native(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', arriving)
    Logger(tmp_path)
    assert arrivals == [True]
    assert target.read_bytes() == b'prior error evidence\n'


def test_logger_constructor_still_rejects_directory(tmp_path):
    from rpipe.structure import artifacts as kinds

    target = tmp_path / kinds.RUN_LOG
    target.mkdir(parents=True)
    with pytest.raises(OSError):
        Logger(tmp_path)


def test_logger_constructor_does_not_reopen_existing_evidence(tmp_path, monkeypatch):
    from rpipe.structure import artifacts as kinds

    target = tmp_path / kinds.RUN_LOG
    target.parent.mkdir(parents=True)
    target.write_bytes(b'existing diagnostic bytes\n')
    native = Path.open

    def no_reopen(path, *args, **kwargs):
        if path == target:
            raise PermissionError('existing evidence must not be reopened by initialization')
        return native(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, 'open', no_reopen)
        Logger(tmp_path)
    assert target.read_bytes() == b'existing diagnostic bytes\n'


@pytest.mark.parametrize('phase', ['write', 'flush'])
@pytest.mark.parametrize('outcome', ['error', 'cancel', 'normal', 'programming'])
def test_logger_stream_close_preserves_write_or_flush_failure(tmp_path, stream_close_fault, phase, outcome):
    import errno
    logger = Logger(tmp_path)
    logger.path.write_bytes(b'old log evidence\n')
    peer = tmp_path / 'peer.log'
    peer.write_bytes(b'peer evidence')
    primary = KeyboardInterrupt('cancel log') if outcome == 'cancel' else None if outcome == 'normal' else OSError(errno.ENOSPC, 'log IO')
    secondary = RuntimeError('unexpected close error') if outcome == 'programming' else OSError(errno.EIO, 'close IO')
    expected = secondary if outcome in ('normal', 'programming') else primary
    handles, calls = stream_close_fault(lambda path, mode: path == logger.path and mode == 'a', primary, secondary, phase=phase)
    with pytest.raises(type(expected)) as caught:
        logger.info('new log event')
    assert caught.value is expected
    assert len(handles) == 1 and handles[0].closed and calls.count('close') == 1
    assert logger.path.read_bytes().startswith(b'old log evidence\n')
    assert peer.read_bytes() == b'peer evidence'
    assert calls[0] == 'write' and (phase != 'flush' or 'flush' in calls)


@pytest.mark.parametrize('reopen', [False, True], ids=['same-logger', 'reopened-logger'])
@pytest.mark.parametrize('tail', ['ascii-cut', 'utf8-cut', 'lf', 'crlf', 'cr', 'empty'])
def test_logger_keeps_interrupted_tail_separate_from_next_record(tmp_path, capsys, reopen, tail):
    import os
    logger = Logger(tmp_path)
    header = b'2001-01-01T00:00:00.000+00:00 INFO  - [flow] interrupted '
    prefixes = {'ascii-cut': header, 'utf8-cut': header + b'\xe4',
                'lf': header + b'\n', 'crlf': header + b'\r\n', 'cr': header + b'\r', 'empty': b''}
    before = prefixes[tail]
    logger.path.write_bytes(before)
    if reopen:
        logger = Logger(tmp_path)
    assert logger.path.read_bytes() == before
    peer = tmp_path / 'peer.bin'
    peer.write_bytes(b'peer')
    logger.error('current failure')
    emitted = capsys.readouterr().out
    assert _HEADER.match(emitted)
    separator = os.linesep.encode() if tail.endswith('cut') else b''
    assert logger.path.read_bytes() == before + separator + emitted.replace('\n', os.linesep).encode()
    assert peer.read_bytes() == b'peer'


@pytest.mark.parametrize('error_type', [PermissionError, FileNotFoundError])
def test_optional_log_tail_inspection_keeps_append_only_access(tmp_path, monkeypatch, capsys, error_type):
    import os
    logger = Logger(tmp_path)
    before = b'existing record\n'
    logger.path.write_bytes(before)
    native = Path.open

    def append_only(path, *args, **kwargs):
        if path == logger.path and args and args[0] == 'rb':
            raise error_type('tail unavailable')
        return native(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', append_only)
        logger.error('still appendable')
    emitted = capsys.readouterr().out
    assert logger.path.read_bytes() == before + emitted.replace('\n', os.linesep).encode()


@pytest.mark.parametrize('kind,separator', [
    ('info', '\n'), ('warning', '\n'), ('error', '\n'),
    ('exception-context', '\n'), ('exception-value', '\n'), ('report', '\n'),
    ('info', '\r\n'), ('info', '\u2028'),
])
def test_multiline_logger_body_keeps_complete_record_identity(tmp_path, capsys, kind, separator):
    logger = Logger(tmp_path)
    message = 'first body line' + separator + '[error] continuation body text'
    if kind == 'exception-context':
        logger.exception(message, ValueError('original failure'))
    elif kind == 'exception-value':
        logger.exception('context', ValueError(message))
    elif kind == 'report':
        logger.report(AlgorithmTracker(tmp_path / 'tracker'), 'train', {'epoch': 1, 'detail': message})
    else:
        getattr(logger, kind)(message)
    text = logger.path.read_text(encoding='utf-8')
    assert text == capsys.readouterr().out
    lines = text.splitlines()
    assert lines and all(_HEADER.match(line) for line in lines)
    event = {'info': 'flow', 'warning': 'warn', 'error': 'error',
             'exception-context': 'error', 'exception-value': 'error', 'report': 'epoch'}[kind]
    assert all(f' - [{event}]' in line for line in lines)
    assert 'first body line' in text and 'continuation body text' in text


@pytest.mark.parametrize('message', ['', 'ordinary single line'])
def test_single_line_logger_format_stays_unchanged(tmp_path, capsys, message):
    logger = Logger(tmp_path)
    logger.info(message)
    text = logger.path.read_text(encoding='utf-8')
    assert text == capsys.readouterr().out
    assert len(text.splitlines()) == 1
    assert text.endswith((' - [flow] ' + message).rstrip() + '\n')


def test_logger_formats_message_object_only_once(tmp_path, capsys):
    class Message:
        calls = 0

        def __format__(self, spec):
            assert spec == ''
            self.calls += 1
            return 'existing f-string conversion'

    message = Message()
    logger = Logger(tmp_path)
    logger.info(message)
    assert message.calls == 1
    assert logger.path.read_text(encoding='utf-8') == capsys.readouterr().out


@contextlib.contextmanager
def _native_logger_stdout(kind):
    reader, writer = os.pipe()
    raw = os.fdopen(writer, 'wb', buffering=0)
    stream = io.TextIOWrapper(raw, encoding='utf-8', write_through=True, newline='\n')
    errors, captured = [], []
    class Sink:
        def write(self, text):
            try:
                return stream.write(text)
            except (OSError, ValueError) as error:
                errors.append(error)
                raise
        def flush(self):
            try:
                return stream.flush()
            except (OSError, ValueError) as error:
                errors.append(error)
                raise
    try:
        if kind == 'pipe':
            os.close(reader)
            reader = None
        elif kind == 'closed':
            stream.close()
        yield Sink(), errors, captured
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            raw.close()
        if reader is not None:
            try:
                captured.append(os.read(reader, 65536).decode('utf-8'))
            finally:
                os.close(reader)


def _emit_sink_probe(logger, kind):
    if kind == 'exception':
        def inner():
            raise RuntimeError('original diagnostic cause')
        try:
            inner()
        except RuntimeError as error:
            logger.exception('diagnostic context', error)
        return ['diagnostic context', 'Traceback (most recent call last):', 'RuntimeError: original diagnostic cause']
    if kind == 'report':
        logger.report(None, 'train', {'epoch': 1, 'note': 'first report\nlast report'})
        return ['first report', 'last report']
    text = 'first record\nlast record' if kind == 'multiline' else 'single diagnostic'
    method = logger.info if kind in ('info', 'multiline') else getattr(logger, kind)
    method(text)
    return ['first record', 'last record'] if kind == 'multiline' else ['single diagnostic']


@pytest.mark.parametrize('kind', ['info', 'multiline', 'warning', 'error', 'exception', 'report'])
@pytest.mark.parametrize('sink_kind', ['open', 'pipe', 'closed'])
def test_logger_native_stdout_refusal_still_persists_complete_records(tmp_path, kind, sink_kind):
    logger = Logger(tmp_path / 'runs/owner/assets')
    prefix = b'preserved old log\n'
    logger.path.write_bytes(prefix)
    peer = tmp_path / 'peer.bin'
    peer.write_bytes(b'peer evidence')
    expected = {'exception': ['diagnostic context', 'Traceback (most recent call last):', 'RuntimeError: original diagnostic cause'],
                'multiline': ['first record', 'last record'], 'report': ['first report', 'last report']}.get(kind, ['single diagnostic'])
    with _native_logger_stdout(sink_kind) as (sink, errors, captured):
        with contextlib.redirect_stdout(sink):
            if sink_kind == 'open':
                _emit_sink_probe(logger, kind)
            else:
                with pytest.raises((OSError, ValueError)) as caught:
                    _emit_sink_probe(logger, kind)
                assert caught.value is errors[0]
    text = logger.path.read_text(encoding='utf-8')
    assert all(value in text for value in expected)
    assert logger.path.read_bytes().startswith(prefix)
    assert peer.read_bytes() == b'peer evidence'
    if sink_kind == 'open':
        assert not errors
        assert logger.path.read_bytes() == prefix + captured[0].replace('\n', os.linesep).encode('utf-8')


@pytest.mark.parametrize('kind', ['multiline', 'exception'])
@pytest.mark.parametrize('phase', ['open', 'write', 'flush', 'close'])
def test_logger_disk_refusal_still_emits_complete_stdout(tmp_path, monkeypatch, stream_close_fault, kind, phase):
    logger = Logger(tmp_path / 'runs/owner/assets')
    logger.path.write_bytes(b'preserved old log\n')
    peer = tmp_path / 'peer.bin'
    peer.write_bytes(b'peer evidence')
    primary = PermissionError('disk sink refused')
    handles = []
    if phase == 'open':
        native = Path.open
        def denied(path, mode='r', *args, **kwargs):
            if path == logger.path and mode == 'a':
                raise primary
            return native(path, mode, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', denied)
    else:
        handles, _ = stream_close_fault(lambda path, mode: path == logger.path and mode == 'a',
                                        None if phase == 'close' else primary,
                                        primary if phase == 'close' else None,
                                        phase='write' if phase == 'close' else phase)
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        with pytest.raises(PermissionError) as caught:
            _emit_sink_probe(logger, kind)
    assert caught.value is primary
    expected = ['first record', 'last record'] if kind == 'multiline' else ['diagnostic context', 'Traceback (most recent call last):', 'RuntimeError: original diagnostic cause']
    assert all(value in sink.getvalue() for value in expected)
    assert all(handle.closed for handle in handles)
    assert logger.path.read_bytes().startswith(b'preserved old log\n')
    assert peer.read_bytes() == b'peer evidence'


def test_logger_dual_refusal_preserves_first_io_and_closes_owned_files(tmp_path, stream_close_fault):
    logger = Logger(tmp_path)
    logger.path.write_bytes(b'old log\n')
    disk_error = PermissionError('disk second')
    handles, calls = stream_close_fault(lambda path, mode: path == logger.path and mode == 'a', disk_error, None)
    with _native_logger_stdout('pipe') as (sink, errors, _):
        with contextlib.redirect_stdout(sink):
            with pytest.raises(OSError) as caught:
                _emit_sink_probe(logger, 'multiline')
        assert caught.value is errors[0]
    assert len(handles) == 2 and all(handle.closed for handle in handles)
    assert calls.count('close') == 2
    assert logger.path.read_bytes().startswith(b'old log\n')


def test_logger_non_io_stdout_bug_remains_strict(tmp_path):
    logger = Logger(tmp_path)
    before = logger.path.read_bytes()
    error = RuntimeError('unexpected stdout implementation bug')
    class Broken:
        def write(self, value):
            raise error
        def flush(self):
            pass
    with contextlib.redirect_stdout(Broken()):
        with pytest.raises(RuntimeError) as caught:
            _emit_sink_probe(logger, 'multiline')
    assert caught.value is error
    assert logger.path.read_bytes() == before


def test_optional_log_tail_exit_never_swallows_cancellation(tmp_path, reader_exit_case, reader_close_fault, capsys):
    from rpipe.payloads.logger import Logger
    case = reader_exit_case
    logger = Logger(tmp_path / 'assets')
    payload = b'previous complete record\n'
    logger.path.write_bytes(payload)
    peer = tmp_path / 'peer'
    peer.write_bytes(b'peer untouched')
    expected = case.expected if case.kind in ('cancel', 'body-programming', 'close-programming') else None
    with reader_close_fault(logger.path, case, 'rb') as (handles, closes):
        if expected is not None:
            with pytest.raises(type(expected)) as caught:
                logger.info('new synthetic record')
            assert caught.value is expected
        else:
            logger.info('new synthetic record')
    assert len(handles) == len(closes) == 1 and handles[0].closed
    after = logger.path.read_bytes()
    assert after.startswith(payload) and peer.read_bytes() == b'peer untouched'
    assert (b'new synthetic record' in after) is (expected is None)
    assert 'new synthetic record' in capsys.readouterr().out


@pytest.mark.parametrize('elapsed,eta,expected', [
    ('a', 'b', '[time] elapsed=a eta=b'),
    (0, '', '[time] elapsed=0 eta='),
    (None, False, '[time] eta=False'),
])
def test_report_time_fields_keep_fixed_order_and_non_none_values(tmp_path, monkeypatch, elapsed, eta, expected):
    logger = Logger(tmp_path)
    rows = []
    monkeypatch.setattr(logger, '_emit', lambda *row: rows.append(row))
    logger.report(None, 'test', extra={'eta': eta, 'elapsed': elapsed})
    assert rows == [('INFO', 'split', '[split] test ' + expected)]

"""Persisted failure context excludes arbitrary exception text and source paths."""
import json

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


def test_error_record_keeps_types_and_frames_without_private_exception_text():
    from rpipe.structure.artifact.io import safe_error_record

    try:
        try:
            raise ValueError('password=secret-value document=private-original')
        except ValueError as cause:
            raise RuntimeError(r'C:\private\person\source.py token=hidden-key') from cause
    except RuntimeError as error:
        record = safe_error_record(error)
    text = json.dumps(record)
    assert record['type'] == 'RuntimeError'
    assert record['message'] == '[redacted]'
    assert record['causes'][0]['type'] == 'ValueError'
    assert 'test_error_record_keeps_types' in record['traceback']
    for secret in ('secret-value', 'private-original', 'person', 'hidden-key', __file__):
        assert secret not in text


def test_error_record_never_calls_exception_string_conversion():
    from rpipe.structure.artifact.io import safe_error_record

    class BrokenString(Exception):
        def __str__(self):
            raise AssertionError('formatter must not call custom exception __str__')

    record = safe_error_record(BrokenString())
    assert record['type'] == 'BrokenString'
    assert record['message'] == '[redacted]'


def test_worker_error_record_is_normalized_without_trusting_remote_text():
    from rpipe.structure.artifact.io import safe_error_record

    error = RuntimeError('local-private')
    error.record = {'type': 'LookupError', 'message': 'token=remote-private',
                    'traceback': 'File C:/private/source.py',
                    'frames': [{'module': 'worker', 'function': 'execute', 'line': 42}],
                    'causes': [{'type': 'ValueError', 'message': 'password=hidden'}]}
    record = safe_error_record(error)
    assert record['type'] == 'LookupError'
    assert 'worker.execute:42' in record['traceback']
    assert record['causes'][0]['type'] == 'ValueError'
    assert 'private' not in json.dumps(record)
    assert 'hidden' not in json.dumps(record)


def test_error_cycles_and_cleanup_lists_have_a_finite_diagnostic_budget():
    from rpipe.structure.artifact.io import safe_error_record

    error = RuntimeError('private')
    error.__cause__ = error
    error.errors = tuple(ValueError(str(i)) for i in range(100))
    record = safe_error_record(error)
    assert record['type'] == 'RuntimeError'
    assert len(record['causes']) <= 8
    assert 'causes' in record['truncated_fields']
    assert len(json.dumps(record)) < 65536


@pytest.mark.parametrize('code', [17, True, 'secret'])
def test_worker_exit_code_preserves_only_actual_integer_fact(code):
    from rpipe.structure.artifact.io import safe_error_record

    error = RuntimeError('private')
    error.record = {'type': 'ChildExit', 'message': 'private', 'exit_code': code}
    record = safe_error_record(error)
    assert ('exit_code' in record) is (type(code) is int)
    if type(code) is int:
        assert record['exit_code'] == code

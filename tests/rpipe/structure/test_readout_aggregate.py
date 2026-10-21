import json
from pathlib import Path

import pytest

from rpipe.structure.readout import load_curve_series, load_tracker_history
from rpipe.structure import artifacts as kinds

pytestmark = [
    pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
    pytest.mark.flow_layer, pytest.mark.module_process,
    pytest.mark.cost(cost_class='c1'),
    pytest.mark.result_type('categorical', detail='summary'),
]

_BAD_ENCODING = [b'\xff\xfe\x80', b'{"splits": "\x80"}', b'\xf0\x9f']


@pytest.mark.parametrize('raw', _BAD_ENCODING)
@pytest.mark.parametrize('primary', [False, True], ids=['fallback-only', 'valid-jsonl'])
@pytest.mark.parametrize('reader', [load_curve_series, load_tracker_history])
def test_optional_state_decode_failure_keeps_valid_trajectory(tmp_path, raw, primary, reader):
    assets = tmp_path / 'runs/a/assets'
    state = assets / kinds.TRACKER_STATE
    state.parent.mkdir(parents=True)
    state.write_bytes(raw)
    scalar = assets / kinds.TRACKER_JSONL
    if primary:
        scalar.parent.mkdir(parents=True, exist_ok=True)
        scalar.write_text(json.dumps({'split': 'test', 'name': 'Accuracy', 'mean': 75.0,
                                      'optimizer_step': 1}) + '\n', encoding='utf-8')
    before = {p: p.read_bytes() for p in assets.rglob('*') if p.is_file()}
    body = reader(tmp_path, 'a')
    if primary:
        expected = [75.0] if reader is load_tracker_history else {
            'unit': 'step', 'x': [1], 'values': [75.0]}
        assert body == {'test': {'Accuracy': expected}}
    else:
        assert body == {}
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('raw', [b'{', b'[]', b'{}',
    b'{"splits":{"test":{"Accuracy":{"history":[10.0,20.0]}}}}'])
def test_optional_state_legacy_fallback_contract(tmp_path, raw):
    state = tmp_path / 'runs/a/assets' / kinds.TRACKER_STATE
    state.parent.mkdir(parents=True)
    state.write_bytes(raw)
    body = load_curve_series(tmp_path, 'a')
    expected = {'test': {'Accuracy': {'unit': 'observation', 'x': [1, 2],
                                     'values': [10.0, 20.0]}}} if b'history' in raw else {}
    assert body == expected
    assert state.read_bytes() == raw


@pytest.mark.parametrize('reader', [load_curve_series, load_tracker_history])
@pytest.mark.parametrize('primary', [False, True], ids=['legacy-only', 'valid-jsonl'])
@pytest.mark.parametrize('operation', ['stat', 'read_text'])
@pytest.mark.parametrize('error_kind', ['permission', 'io', 'missing', 'programming'])
def test_optional_state_keeps_io_and_programming_error_boundary(
        tmp_path, monkeypatch, reader, primary, operation, error_kind):
    operation = 'open' if operation == 'read_text' else operation
    state = tmp_path / 'runs/a/assets' / kinds.TRACKER_STATE
    state.parent.mkdir(parents=True)
    state.write_text('{}', encoding='utf-8')
    if primary:
        scalar = tmp_path / 'runs/a/assets' / kinds.TRACKER_JSONL
        scalar.parent.mkdir(parents=True, exist_ok=True)
        scalar.write_text(json.dumps({'split': 'test', 'name': 'Accuracy', 'mean': 75.0,
                                      'optimizer_step': 1}) + '\n', encoding='utf-8')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    error = {'permission': PermissionError(13, 'optional state denied'),
             'io': OSError(5, 'optional state I/O refusal'),
             'missing': FileNotFoundError(2, 'optional state disappeared'),
             'programming': RuntimeError('unexpected reader bug')}[error_kind]
    original = getattr(Path, operation)
    def read(path, *args, **kwargs):
        if kwargs.get('mode', args[0] if args else 'r') not in ('r', 'rt'):
            return original(path, *args, **kwargs)
        if path == state:
            raise error
        return original(path, *args, **kwargs)
    with monkeypatch.context() as fault:
        fault.setattr(Path, operation, read)
        if error_kind == 'programming':
            with pytest.raises(RuntimeError) as caught:
                reader(tmp_path, 'a')
            assert caught.value is error
        else:
            values = [75.0] if reader is load_tracker_history else {
                'unit': 'step', 'x': [1], 'values': [75.0]}
            assert reader(tmp_path, 'a') == ({'test': {'Accuracy': values}} if primary else {})
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('reader', [load_curve_series, load_tracker_history])
@pytest.mark.parametrize('primary', [False, True], ids=['legacy-only', 'valid-jsonl'])
@pytest.mark.parametrize('payload', ['valid-integer', 'integer-limit', 'deep-json'])
def test_optional_state_integer_decode_keeps_primary_and_legacy(tmp_path, reader, primary, payload):
    import sys

    limit = getattr(sys, 'get_int_max_str_digits', lambda: 0)()
    large = payload != 'valid-integer'
    if payload == 'integer-limit' and not 0 < limit < 5000:
        pytest.skip('native integer digit limit is unavailable or outside the 5000-digit probe')
    assets = tmp_path / 'runs/a/assets'
    state = assets / kinds.TRACKER_STATE
    state.parent.mkdir(parents=True)
    raw = (b'{"splits":{"test":{"Accuracy":{"history":[10.0,20.0]}}},"unused":'
           + (b'[' * 10000 + b'0' + b']' * 10000 if payload == 'deep-json'
                    else b'9' * 5000 if large else b'17') + b'}')
    state.write_bytes(raw)
    scalar = assets / kinds.TRACKER_JSONL
    if primary:
        scalar.parent.mkdir(parents=True, exist_ok=True)
        scalar.write_text(json.dumps({'split': 'test', 'name': 'Accuracy', 'mean': 75.0,
                                      'optimizer_step': 1}) + '\n', encoding='utf-8')
    before = {p: p.read_bytes() for p in assets.rglob('*') if p.is_file()}
    if not primary and large:
        expected = {}
    else:
        values = [75.0] if primary else [10.0, 20.0]
        series = values if reader is load_tracker_history else {
            'unit': 'step' if primary else 'observation',
            'x': [1] if primary else [1, 2], 'values': values}
        expected = {'test': {'Accuracy': series}}
    assert reader(tmp_path, 'a') == expected
    assert {p: p.read_bytes() for p in assets.rglob('*') if p.is_file()} == before



@pytest.mark.parametrize('reader', [load_curve_series, load_tracker_history])
@pytest.mark.parametrize('payload', ['valid-integer', 'syntax', 'integer-limit', 'deep-json'])
def test_optional_jsonl_parser_failure_keeps_later_valid_record(tmp_path, reader, payload):
    import sys

    limit = getattr(sys, 'get_int_max_str_digits', lambda: 0)()
    if payload == 'integer-limit' and not 0 < limit < 5000:
        pytest.skip('native integer digit limit is unavailable or outside the 5000-digit probe')
    literal = (b'[' * 10000 + b'0' + b']' * 10000 if payload == 'deep-json'
               else b'9' * 5000 if payload == 'integer-limit' else b'17')
    first = b'{' if payload == 'syntax' else b'{"unused":' + literal + b'}'
    scalar = tmp_path / 'runs/a/assets' / kinds.TRACKER_JSONL
    scalar.parent.mkdir(parents=True)
    raw = first + b'\n' + (json.dumps({'split': 'test', 'name': 'Accuracy', 'mean': 75.0,
                                       'optimizer_step': 1}) + '\n').encode('utf-8')
    scalar.write_bytes(raw)
    expected = [75.0] if reader is load_tracker_history else {
        'unit': 'step', 'x': [1], 'values': [75.0]}
    assert reader(tmp_path, 'a') == {'test': {'Accuracy': expected}}
    assert scalar.read_bytes() == raw



def test_optional_jsonl_keeps_unexpected_decoder_runtime_error(tmp_path, monkeypatch):
    scalar = tmp_path / 'runs/a/assets' / kinds.TRACKER_JSONL
    scalar.parent.mkdir(parents=True)
    scalar.write_bytes(b'{}\n')
    error = RuntimeError('unexpected JSON decoder bug')
    original = json.loads
    def decode(value, *args, **kwargs):
        if value == b'{}\n':
            raise error
        return original(value, *args, **kwargs)
    monkeypatch.setattr(json, 'loads', decode)
    with pytest.raises(RuntimeError) as caught:
        load_curve_series(tmp_path, 'a')
    assert caught.value is error
    assert scalar.read_bytes() == b'{}\n'


def _result_read_inputs(root, payload):
    from rpipe.structure.artifacts import write_json as write_result

    index = {'study': 'synthetic', 'experiments': [{'factors': {}, 'runs': [
        {'id': 'bad', 'seed': 0}, {'id': 'peer', 'seed': 1}]}]}
    for run in ('bad', 'peer'):
        write_result(root / 'runs' / run / 'result.json', {
            'status': 'succeeded', 'control': {}, 'metrics': {'accuracy': 42.0}, 'paths': {}})
    bad = root / 'runs/bad/result.json'
    if payload == 'missing':
        bad.unlink()
    elif payload != 'valid':
        raw = {'nonobject': b'[]', 'syntax': b'{', 'utf8': b'\xff',
               'deep': b'{"unused":' + b'[' * 12000 + b'0' + b']' * 12000 + b'}'}[payload]
        bad.write_bytes(raw)
    return index, bad, {f: f.read_bytes() for f in root.rglob('*') if f.is_file()}


@pytest.mark.parametrize('payload', ['nonobject', 'deep', 'syntax', 'utf8', 'missing', 'valid'])
def test_optional_result_decode_keeps_peer_and_existing_partial_counts(tmp_path, payload):
    from rpipe.structure.readout import collect_from_index, summarize

    index, bad, before = _result_read_inputs(tmp_path, payload)
    body = summarize(collect_from_index(tmp_path, index), study='synthetic', source_run=None)
    cell = body['experiments'][0]
    count = 2 if payload == 'valid' else 1
    assert cell['n'] == count and cell['n_planned'] == 2
    assert cell['metrics']['accuracy'] == {'mean': 42.0, 'std': 0.0, 'min': 42.0, 'max': 42.0, 'n': count}
    assert body['complete'] is (payload == 'valid')
    assert cell['runs'][0]['status'] == ('succeeded' if payload == 'valid' else None)
    assert {f: f.read_bytes() for f in before} == before


@pytest.mark.parametrize('operation', ['stat', 'read_text'])
@pytest.mark.parametrize('failure', ['permission', 'io', 'missing', 'programming'])
def test_optional_result_stat_read_and_programming_error_boundary(tmp_path, monkeypatch, operation, failure):
    operation = 'open' if operation == 'read_text' else operation
    from rpipe.structure.readout import collect_from_index, summarize

    index, bad, before = _result_read_inputs(tmp_path, 'valid')
    error = {'permission': PermissionError(13, 'result denied'), 'io': OSError(5, 'result IO'),
             'missing': FileNotFoundError(2, 'result disappeared'), 'programming': RuntimeError('reader programming error')}[failure]
    original = getattr(Path, operation)
    def refuse(path, *args, **kwargs):
        if kwargs.get('mode', args[0] if args else 'r') not in ('r', 'rt'):
            return original(path, *args, **kwargs)
        if path == bad:
            raise error
        return original(path, *args, **kwargs)
    with monkeypatch.context() as fault:
        fault.setattr(Path, operation, refuse)
        if failure == 'programming':
            with pytest.raises(RuntimeError) as caught:
                collect_from_index(tmp_path, index)
            assert caught.value is error
        else:
            body = summarize(collect_from_index(tmp_path, index), study='synthetic', source_run=None)
            assert not body['complete']
            assert body['experiments'][0]['n'] == 1 and body['experiments'][0]['n_planned'] == 2
            assert body['experiments'][0]['metrics']['accuracy']['mean'] == 42.0
    assert {f: f.read_bytes() for f in before} == before


@pytest.mark.parametrize('payload,kind', [('nonobject', 'artifact'), ('deep', 'recursion')])
def test_optional_result_fix_does_not_relax_strict_public_loader(tmp_path, payload, kind):
    from rpipe.structure.contracts import AdmissionError as CorruptArtifactError
    from rpipe.structure.artifacts import read_json as load_result

    _, bad, before = _result_read_inputs(tmp_path, payload)
    with pytest.raises(CorruptArtifactError if kind == 'artifact' else RecursionError):
        load_result(bad)
    assert {f: f.read_bytes() for f in before} == before


def test_optional_curve_read_exit_never_swallows_cancellation(tmp_path, reader_exit_case, reader_close_fault):
    case = reader_exit_case
    path = tmp_path / 'runs' / 'r1' / 'assets' / kinds.TRACKER_JSONL
    path.parent.mkdir(parents=True)
    payload = b'{"split":"test","name":"Accuracy","mean":75.0,"optimizer_step":1}\n'
    path.write_bytes(payload)
    peer = tmp_path / 'peer'
    peer.write_bytes(b'peer untouched')
    expected = case.expected if case.kind in ('cancel', 'body-programming', 'close-programming') else None
    with reader_close_fault(path, case, 'rb') as (handles, closes):
        if expected is not None:
            with pytest.raises(type(expected)) as caught:
                load_curve_series(tmp_path, 'r1')
            assert caught.value is expected
        else:
            result = load_curve_series(tmp_path, 'r1')
            assert result == ({} if case.kind == 'read-io' else {
                'test': {'Accuracy': {'unit': 'step', 'x': [1], 'values': [75.0]}}})
    assert len(handles) == len(closes) == 1 and handles[0].closed
    assert path.read_bytes() == payload and peer.read_bytes() == b'peer untouched'


def test_tracker_state_whole_read_preserves_cancel_and_io_fallback(tmp_path, reader_exit_case, reader_close_fault):
    case = reader_exit_case
    path = tmp_path / 'runs' / 'r1' / 'assets' / kinds.TRACKER_STATE
    path.parent.mkdir(parents=True)
    payload = b'{"splits":{"test":{"Accuracy":{"history":[75.0]}}}}'
    path.write_bytes(payload)
    peer = tmp_path / 'peer'
    peer.write_bytes(b'peer unchanged')
    expected = case.expected if case.kind in ('cancel', 'close-programming') else None
    with reader_close_fault(path, case, 'r') as (handles, closes):
        if expected is not None:
            with pytest.raises(type(expected)) as caught:
                load_tracker_history(tmp_path, 'r1')
            assert caught.value is expected
        else:
            assert load_tracker_history(tmp_path, 'r1') == ({'test':{'Accuracy':[75.0]}}
                                                           if case.kind == 'success' else {})
    assert len(handles) == len(closes) == 1 and handles[0].closed
    assert path.read_bytes() == payload and peer.read_bytes() == b'peer unchanged'

from pathlib import Path
import pytest

from rpipe.structure.artifacts import atomic_replace, atomic_write_text

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_artifact,
              pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('winerror', [5, 32, 33])
def test_atomic_replace_transient_windows_denial_eventually_commits(tmp_path, monkeypatch, winerror):
    source, target = tmp_path / 'new', tmp_path / 'old'
    source.write_text('new', encoding='utf-8')
    target.write_text('old', encoding='utf-8')
    replace, pauses, calls = Path.replace, [], []

    def held(path, destination):
        calls.append(path)
        if len(calls) <= 2:
            assert target.read_text(encoding='utf-8') == 'old'
            error = PermissionError('file temporarily held')
            error.winerror = winerror
            raise error
        return replace(path, destination)

    monkeypatch.setattr(Path, 'replace', held)
    monkeypatch.setattr('rpipe.structure.artifacts.time.sleep', pauses.append)
    assert atomic_replace(source, target) == target
    assert target.read_text(encoding='utf-8') == 'new' and not source.exists()
    assert len(calls) == 3 and pauses == [0.05, 0.1]


@pytest.mark.parametrize('winerror,expected_calls', [(5, 6), (None, 1), (112, 1)])
def test_atomic_write_permanent_failure_preserves_target_and_cleans_stage(tmp_path, monkeypatch, winerror, expected_calls):
    target, calls = tmp_path / 'old', []
    target.write_text('old', encoding='utf-8')
    error = PermissionError('permanent failure')
    error.winerror = winerror

    def denied(path, destination):
        calls.append(path)
        raise error

    monkeypatch.setattr(Path, 'replace', denied)
    monkeypatch.setattr('rpipe.structure.artifacts.time.sleep', lambda _: None)
    with pytest.raises(PermissionError) as caught:
        atomic_write_text(target, 'new')
    assert caught.value is error and len(calls) == expected_calls
    assert target.read_text(encoding='utf-8') == 'old'
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('error_type', [OSError, KeyboardInterrupt])
def test_partial_text_write_failure_cleans_only_its_stage(tmp_path, monkeypatch, stream_close_fault, existing, error_type):
    target = tmp_path / 'result.txt'
    if existing:
        target.write_text('old', encoding='utf-8')
    peer = tmp_path / 'result.txt.other.tmp'
    peer.write_text('another writer', encoding='utf-8')
    error = error_type('injected partial text write failure')
    stream_close_fault(lambda path, mode: path.suffix == '.tmp' and mode == 'w', error, None)
    with pytest.raises(error_type) as caught:
        atomic_write_text(target, 'new')
    assert caught.value is error
    assert peer.read_text(encoding='utf-8') == 'another writer'
    assert set(tmp_path.iterdir()) == ({target, peer} if existing else {peer})
    if existing:
        assert target.read_text(encoding='utf-8') == 'old'


@pytest.mark.parametrize('existing', [False, True])
def test_text_encoding_failure_preserves_target_and_peer(tmp_path, existing):
    target, peer = tmp_path / 'result.txt', tmp_path / 'peer.tmp'
    if existing:
        target.write_bytes(b'original')
    peer.write_bytes(b'peer staging')
    with pytest.raises(UnicodeEncodeError):
        atomic_write_text(target, 'plain\u4e2d\u6587', encoding='ascii')
    assert set(tmp_path.iterdir()) == ({target, peer} if existing else {peer})
    assert peer.read_bytes() == b'peer staging'
    if existing:
        assert target.read_bytes() == b'original'


def test_atomic_text_success_replaces_target_and_keeps_peer_stage(tmp_path):
    target = tmp_path / 'result.txt'
    target.write_text('old', encoding='utf-8')
    peer = tmp_path / 'result.txt.other.tmp'
    peer.write_text('another writer', encoding='utf-8')
    assert atomic_write_text(target, 'new') == target
    assert target.read_text(encoding='utf-8') == 'new'
    assert peer.read_text(encoding='utf-8') == 'another writer'
    assert set(tmp_path.iterdir()) == {target, peer}


# Exact protocol exemptions: append offsets, checkpoint recovery and lock inode
# ownership cannot use whole-file text replacement (design section 5.3).










def test_index_encoding_failure_does_not_truncate_existing_plan(tmp_path):
    from rpipe.structure.artifacts import write_json

    target = tmp_path / 'index.json'
    target.write_bytes(b'old plan')
    with pytest.raises(TypeError):
        write_json(tmp_path / 'index.json', {'id': 'known', 'study': 'new', 'bad': object()})
    assert target.read_bytes() == b'old plan'


@pytest.mark.parametrize('newline', [None, '\n'])
def test_atomic_text_newline_option_preserves_native_and_script_bytes(tmp_path, newline):
    import os

    target = tmp_path / 'unicode.txt'
    atomic_write_text(target, '中文\nsecond\n', newline=newline)
    assert target.read_bytes() == ('中文' + (os.linesep if newline is None else '\n')
                                  + 'second' + (os.linesep if newline is None else '\n')).encode('utf-8')


@pytest.mark.skipif(__import__('os').name == 'nt', reason='POSIX executable mode bits')
def test_atomic_text_preserves_existing_script_mode(tmp_path):
    target = tmp_path / 'launch.sh'
    target.write_text('old', encoding='utf-8')
    target.chmod(0o750)
    atomic_write_text(target, 'new', newline='\n')
    assert target.stat().st_mode & 0o777 == 0o750














@pytest.mark.parametrize('kind', ['peer', 'target-alias', 'stat-refusal'])
def test_text_stage_collision_preserves_unowned_bytes(tmp_path, monkeypatch, kind):
    from types import SimpleNamespace
    from rpipe.structure import artifacts as _atomic
    monkeypatch.setattr(_atomic.uuid, 'uuid4', lambda: SimpleNamespace(hex='0' * 32))
    peer = tmp_path / ('0' * 32 + '.tmp')
    target = peer if kind == 'target-alias' else tmp_path / 'target.txt'
    target.write_bytes(b'old target')
    if peer != target:
        peer.write_bytes(b'peer stage')
    before = {p: p.read_bytes() for p in [target, peer]}
    error = PermissionError('target stat refused')
    if kind == 'stat-refusal':
        stat = Path.stat

        def denied(path, *a, **kw):
            if path == target:
                raise error
            return stat(path, *a, **kw)
        monkeypatch.setattr(Path, 'stat', denied)
    with pytest.raises(PermissionError if kind == 'stat-refusal' else FileExistsError) as caught:
        _atomic.atomic_write_text(target, 'new target')
    if kind == 'stat-refusal':
        assert caught.value is error
    assert {p: p.read_bytes() for p in before} == before


def test_concurrent_nonce_collision_cannot_delete_an_active_writer_stage(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    import threading
    from rpipe.structure import artifacts as _atomic

    target = tmp_path / 'target.txt'
    target.write_text('old', encoding='utf-8')
    stage = tmp_path / ('0' * 32 + '.tmp')
    monkeypatch.setattr(_atomic.uuid, 'uuid4', lambda: SimpleNamespace(hex='0' * 32))
    ready, release = threading.Event(), threading.Event()
    publish = _atomic.atomic_replace

    def hold(source, destination):
        if ready.is_set():
            raise AssertionError('colliding writer reached publication')
        ready.set()
        assert release.wait(5)
        return publish(source, destination)

    monkeypatch.setattr(_atomic, 'atomic_replace', hold)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(_atomic.atomic_write_text, target, 'first')
        try:
            assert ready.wait(5)
            with pytest.raises(FileExistsError):
                _atomic.atomic_write_text(target, 'second')
            assert stage.read_text(encoding='utf-8') == 'first'
        finally:
            release.set()
        assert first.result() == target
    assert target.read_text(encoding='utf-8') == 'first'
    assert not stage.exists()


def test_owned_text_stage_chmod_refusal_preserves_old_target_and_peer(tmp_path, monkeypatch):
    from rpipe.structure import artifacts as _atomic

    target, peer = tmp_path / 'target.txt', tmp_path / 'peer.tmp'
    target.write_bytes(b'old target')
    peer.write_bytes(b'peer evidence')
    error = PermissionError('stage mode update refused')
    stages = []

    def refused(path, mode, *args, **kwargs):
        stages.append(path)
        assert path not in (target, peer) and path.read_bytes() == b'new target'
        raise error

    monkeypatch.setattr(Path, 'chmod', refused)
    with pytest.raises(PermissionError) as caught:
        _atomic.atomic_write_text(target, 'new target')
    assert caught.value is error and len(stages) == 1
    assert target.read_bytes() == b'old target' and peer.read_bytes() == b'peer evidence'
    assert set(tmp_path.iterdir()) == {target, peer}


@pytest.mark.parametrize('writer', ['text', 'config', 'result'])
@pytest.mark.parametrize('readonly', [False, True], ids=['writable-control', 'native-readonly'])
def test_atomic_text_native_permissions_cleanup_owned_stage(tmp_path, monkeypatch, writer, readonly):
    import os
    from rpipe.structure import artifacts as _atomic
    from rpipe.structure.artifacts import write_config
    from rpipe.structure.artifacts import write_json as write_result

    if readonly and os.name != 'nt':
        pytest.skip('Windows read-only replacement and unlink semantics')
    target, peer = tmp_path / 'target.txt', tmp_path / 'peer.tmp'
    target.write_bytes(b'old evidence')
    peer.write_bytes(b'other writer evidence')
    peer.chmod(0o444)
    if readonly:
        target.chmod(0o444)
    before = {p: (p.read_bytes(), p.stat().st_mode) for p in (target, peer)}
    monkeypatch.setattr(_atomic.time, 'sleep', lambda _: None)
    def write():
        if writer == 'config':
            return write_config(target, {'new': 'configuration'})
        if writer == 'result':
            return write_result(target, {'status': 'succeeded', 'control': {}, 'metrics': {}, 'paths': {}})
        return _atomic.atomic_write_text(target, 'new text')
    if readonly:
        with pytest.raises(PermissionError):
            write()
        assert (target.read_bytes(), target.stat().st_mode) == before[target]
    else:
        assert write() == target and target.read_bytes() != before[target][0]
    assert (peer.read_bytes(), peer.stat().st_mode) == before[peer]
    assert set(tmp_path.iterdir()) == {target, peer}


@pytest.mark.parametrize('cleanup', ['recover', 'chmod-denied', 'unlink-denied', 'io-denied'])
def test_atomic_text_permission_cleanup_is_owned_bounded_and_best_effort(tmp_path, monkeypatch, cleanup):
    from rpipe.structure import artifacts as _atomic

    target, peer = tmp_path / 'target.txt', tmp_path / 'peer.tmp'
    target.write_bytes(b'old target')
    peer.write_bytes(b'peer bytes')
    target.chmod(0o444)
    peer.chmod(0o444)
    before = {p: (p.read_bytes(), p.stat().st_mode) for p in (target, peer)}
    error = PermissionError('publication refused')
    stages, removals, modes = [], [], []
    unlink, chmod = Path.unlink, Path.chmod
    def refuse(stage, destination):
        stages.append(stage)
        raise error
    def remove(path, *args, **kwargs):
        assert path not in (target, peer)
        removals.append(path)
        if cleanup == 'io-denied':
            raise OSError('cleanup IO failure')
        if cleanup == 'unlink-denied' or not path.stat().st_mode & 0o200:
            raise PermissionError('read-only staging')
        return unlink(path, *args, **kwargs)
    def mode(path, value, *args, **kwargs):
        assert path not in (target, peer)
        modes.append(value)
        if cleanup == 'chmod-denied' and value == 0o600:
            raise PermissionError('cleanup mode refused')
        return chmod(path, value, *args, **kwargs)
    monkeypatch.setattr(_atomic, 'atomic_replace', refuse)
    monkeypatch.setattr(Path, 'unlink', remove)
    monkeypatch.setattr(Path, 'chmod', mode)
    with pytest.raises(PermissionError) as caught:
        _atomic.atomic_write_text(target, 'new candidate')
    assert caught.value is error and len(stages) == 1
    assert {p: (p.read_bytes(), p.stat().st_mode) for p in before} == before
    if cleanup == 'recover':
        assert set(tmp_path.iterdir()) == {target, peer}
        assert len(removals) == 2 and modes[-1] == 0o600
    else:
        assert stages[0].read_bytes() == b'new candidate'
        assert len(removals) == (2 if cleanup == 'unlink-denied' else 1)


@pytest.mark.parametrize('existing', [False, True])
def test_atomic_text_readonly_collision_never_changes_unowned_stage(tmp_path, monkeypatch, existing):
    from types import SimpleNamespace
    from rpipe.structure import artifacts as _atomic

    target, stage = tmp_path / 'target.txt', tmp_path / 'occupied.tmp'
    stage.write_bytes(b'other writer candidate')
    stage.chmod(0o444)
    if existing:
        target.write_bytes(b'old evidence')
    before = {p: (p.read_bytes(), p.stat().st_mode) for p in tmp_path.iterdir()}
    monkeypatch.setattr(_atomic.uuid, 'uuid4', lambda: SimpleNamespace(hex='occupied'))
    with pytest.raises(FileExistsError):
        _atomic.atomic_write_text(target, 'new candidate')
    assert set(tmp_path.iterdir()) == set(before)
    assert {p: (p.read_bytes(), p.stat().st_mode) for p in before} == before


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('outcome', ['error', 'cancel', 'normal', 'programming'])
def test_atomic_stream_close_preserves_write_error_and_cancel(tmp_path, stream_close_fault, existing, outcome):
    import errno
    target, peer = tmp_path / 'result.txt', tmp_path / 'peer.tmp'
    if existing:
        target.write_bytes(b'old target')
    peer.write_bytes(b'peer evidence')
    primary = KeyboardInterrupt('cancel text write') if outcome == 'cancel' else None if outcome == 'normal' else OSError(errno.ENOSPC, 'partial text write')
    secondary = RuntimeError('unexpected close error') if outcome == 'programming' else OSError(errno.EIO, 'close IO')
    expected = secondary if outcome in ('normal', 'programming') else primary
    handles, calls = stream_close_fault(lambda path, mode: path.suffix == '.tmp' and mode == 'w', primary, secondary)
    with pytest.raises(type(expected)) as caught:
        atomic_write_text(target, 'new 中文\n')
    assert caught.value is expected
    assert len(handles) == 1 and handles[0].closed and calls.count('close') == 1
    assert set(tmp_path.iterdir()) == ({target, peer} if existing else {peer})
    assert peer.read_bytes() == b'peer evidence'
    if existing:
        assert target.read_bytes() == b'old target'


@pytest.mark.parametrize('existing', [False, True])
def test_atomic_encoding_error_survives_close_io(tmp_path, stream_close_fault, existing):
    target = tmp_path / 'result.txt'
    if existing:
        target.write_bytes(b'old target')
    handles, calls = stream_close_fault(lambda path, mode: path.suffix == '.tmp' and mode == 'w', None, OSError('close IO'))
    with pytest.raises(UnicodeEncodeError):
        atomic_write_text(target, '中文', encoding='ascii')
    assert len(handles) == 1 and handles[0].closed and calls.count('close') == 1
    assert list(tmp_path.iterdir()) == ([target] if existing else [])
    if existing:
        assert target.read_bytes() == b'old target'












def test_owned_stream_enter_failure_does_not_exit_unentered_manager():
    from rpipe.structure.artifacts import _owned_stream
    failure = RuntimeError('enter failed')
    exits = []
    class Manager:
        def __enter__(self):
            raise failure
        def __exit__(self, *args):
            exits.append(args)
    with pytest.raises(RuntimeError) as caught:
        with _owned_stream(Manager()):
            pytest.fail('body must not execute')
    assert caught.value is failure and exits == []


@pytest.mark.parametrize('suppress', [False, True])
def test_owned_stream_retains_original_enter_value_and_exit_suppression(suppress):
    from rpipe.structure.artifacts import _owned_stream
    value, failure, exits = object(), ValueError('body failed'), []
    class Manager:
        def __enter__(self):
            return value
        def __exit__(self, kind, error, traceback):
            exits.append((kind, error, traceback))
            return suppress
    def body():
        with _owned_stream(Manager()) as actual:
            assert actual is value
            raise failure
    if suppress:
        body()
    else:
        with pytest.raises(ValueError) as caught:
            body()
        assert caught.value is failure
    assert len(exits) == 1 and exits[0][0] is ValueError and exits[0][1] is failure


@pytest.mark.parametrize('writer', ['atomic', 'logger', 'tracker-start', 'tracker-append'])
def test_owned_writers_keep_raw_handle_without_entering_proxy(tmp_path, monkeypatch, writer):
    """Write ownership uses close only; proxy context entry may select another object."""
    from rpipe.structure.algorithm.tracker import AlgorithmTracker
    from rpipe.structure.system.logger import Logger
    from rpipe.structure.artifacts import atomic_write_text
    logger, tracker = Logger(tmp_path / 'log'), AlgorithmTracker(tmp_path / 'tracker')
    tracker.append('train', values={'Loss': 2.0})
    opened, closed = [], []
    original_open = Path.open
    class Writer:
        def __init__(self, raw):
            self.raw = raw
        def __getattr__(self, name):
            return getattr(self.raw, name)
        def __enter__(self):
            pytest.fail('writer ownership must not call proxy __enter__')
        def __exit__(self, *args):
            pytest.fail('writer ownership must not call proxy __exit__')
        def close(self):
            closed.append(self.raw)
            self.raw.close()
    def opening(path, mode='r', *args, **kwargs):
        raw = original_open(path, mode, *args, **kwargs)
        if path.is_relative_to(tmp_path) and mode.startswith(('a', 'w')):
            opened.append(raw)
            return Writer(raw)
        return raw
    monkeypatch.setattr(Path, 'open', opening)
    if writer == 'atomic':
        target = atomic_write_text(tmp_path / 'atomic.txt', 'content')
        assert target.read_bytes() == b'content'
    elif writer == 'logger':
        logger.info('content')
        assert logger.path.read_bytes().rstrip().endswith(b'[flow] content')
    elif writer == 'tracker-start':
        tracker.begin_run()
        assert b'"event": "start"' in tracker.jsonl_path.read_bytes()
    else:
        tracker.append_jsonl('train')
        assert b'"mean": 2.0' in tracker.jsonl_path.read_bytes()
    assert len(opened) == 1 and closed == opened and opened[0].closed

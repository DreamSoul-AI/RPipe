"""Immutable input promotion owns each temporary reference, including concurrent calls."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import threading
import os

import pytest

from rpipe.structure import artifacts as _atomic, artifacts as shared

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_artifact,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('clock', ['real', 'frozen'])
@pytest.mark.parametrize('backend', ['default', 'hardlink'])
def test_concurrent_identical_promotions_own_distinct_staging(tmp_path, monkeypatch, clock, backend):
    source, target = tmp_path / 'source', tmp_path / 'duplicate'
    source.write_bytes(b'immutable payload')
    target.write_bytes(source.read_bytes())
    peer = tmp_path / 'duplicate.peer.link'
    peer.write_bytes(b'peer writer')
    if clock == 'frozen':
        monkeypatch.setattr(shared, 'time', SimpleNamespace(time_ns=lambda: 123, sleep=shared.time.sleep))
    if backend == 'hardlink':
        def unavailable(*args, **kwargs):
            raise OSError('symlinks unavailable')
        monkeypatch.setattr(Path, 'symlink_to', unavailable)
    checked, ready = threading.Barrier(2), threading.Barrier(2)
    digest, replace = shared._digest, _atomic.atomic_replace
    staged = []
    def verify(path):
        value = digest(path)
        if path == target:
            checked.wait(timeout=5)
        return value
    def publish(path, destination):
        staged.append(path)
        ready.wait(timeout=2)
        return replace(path, destination)
    monkeypatch.setattr(shared, '_digest', verify)
    monkeypatch.setattr(_atomic, 'atomic_replace', publish)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(shared.link_input, source, target) for _ in range(2)]
        errors = [future.exception(timeout=10) for future in futures]
    assert errors == [None, None], [repr(error) for error in errors]
    assert [future.result() for future in futures] == [target, target]
    assert len(staged) == len(set(staged)) == 2
    assert all(not path.exists() for path in staged)
    assert source.read_bytes() == target.read_bytes() == b'immutable payload'
    assert target.samefile(source)
    assert peer.read_bytes() == b'peer writer'
    assert set(tmp_path.iterdir()) == {source, target, peer}


def test_failed_reference_publication_preserves_original_and_peer(tmp_path, monkeypatch):
    source, target = tmp_path / 'source', tmp_path / 'duplicate'
    source.write_bytes(b'immutable payload')
    target.write_bytes(source.read_bytes())
    peer = tmp_path / 'duplicate.peer.link'
    peer.write_bytes(b'peer writer')
    def denied(*args, **kwargs):
        raise PermissionError('publication denied')
    monkeypatch.setattr(_atomic, 'atomic_replace', denied)
    with pytest.raises(PermissionError, match='publication denied'):
        shared.link_input(source, target)
    assert source.read_bytes() == target.read_bytes() == b'immutable payload'
    assert not target.samefile(source)
    assert peer.read_bytes() == b'peer writer'
    assert set(tmp_path.iterdir()) == {source, target, peer}


def test_reference_promotion_preserves_valid_long_filename(tmp_path):
    # Extended Windows paths avoid changing the machine's LongPathsEnabled policy.
    root = Path('\\\\?\\' + str(tmp_path.resolve())) if os.name == 'nt' else tmp_path
    source, target = root / 'source', root / ('x' * 220)
    source.write_bytes(b'immutable payload')
    target.write_bytes(source.read_bytes())
    assert shared.link_input(source, target) == target
    assert target.samefile(source)
    assert source.read_bytes() == target.read_bytes() == b'immutable payload'
    assert set(root.iterdir()) == {source, target}


@pytest.mark.parametrize('backend', ['default', 'hardlink'])
@pytest.mark.parametrize('outcome', ['one-fails', 'both-fail'])
def test_failed_publisher_cleans_only_own_stage_while_peer_is_live(tmp_path, monkeypatch, backend, outcome):
    source, target = tmp_path / 'source', tmp_path / 'duplicate'
    source.write_bytes(b'immutable payload')
    target.write_bytes(source.read_bytes())
    peer = tmp_path / 'unrelated.link'
    peer.write_bytes(b'peer writer')
    monkeypatch.setattr(shared, 'time', SimpleNamespace(time_ns=lambda: 123, sleep=shared.time.sleep))
    if backend == 'hardlink':
        def unavailable(*_args, **_kwargs):
            raise OSError('symlinks unavailable')
        monkeypatch.setattr(Path, 'symlink_to', unavailable)
    checked, ready = threading.Barrier(2), threading.Barrier(2)
    cleaned = threading.Event()
    role = threading.local()
    staged = {}
    digest, replace = shared._digest, _atomic.atomic_replace
    def verify(path):
        value = digest(path)
        if path == target:
            checked.wait(timeout=10)
        return value
    def publish(path, destination):
        staged[role.index] = path
        ready.wait(timeout=10)
        if role.index == 0:
            raise PermissionError('publisher zero denied')
        assert cleaned.wait(timeout=10), 'first publisher did not unwind'
        assert staged[0] != path
        assert not staged[0].exists()
        assert path.is_file() and path.samefile(source)
        assert path.read_bytes() == b'immutable payload'
        assert target.read_bytes() == b'immutable payload' and peer.read_bytes() == b'peer writer'
        if outcome == 'both-fail':
            raise PermissionError('publisher one denied')
        return replace(path, destination)
    def promote(index):
        role.index = index
        try:
            return shared.link_input(source, target)
        finally:
            if index == 0:
                cleaned.set()
    monkeypatch.setattr(shared, '_digest', verify)
    monkeypatch.setattr(_atomic, 'atomic_replace', publish)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(promote, index) for index in range(2)]
        errors = [future.exception(timeout=20) for future in futures]
    assert isinstance(errors[0], PermissionError), repr(errors)
    if outcome == 'one-fails':
        assert errors[1] is None, repr(errors[1])
        assert futures[1].result() == target and target.samefile(source)
    else:
        assert isinstance(errors[1], PermissionError), repr(errors)
        assert not target.samefile(source)
    assert len(staged) == len(set(staged.values())) == 2
    assert all(not path.exists() for path in staged.values())
    assert source.read_bytes() == target.read_bytes() == b'immutable payload'
    assert peer.read_bytes() == b'peer writer'
    assert set(tmp_path.iterdir()) == {source, target, peer}


@pytest.mark.parametrize('same_bytes', [False, True])
def test_reference_collision_never_owns_or_deletes_peer(tmp_path, monkeypatch, same_bytes):
    source, target = (tmp_path / 'source', tmp_path / 'target')
    source.write_bytes(b'payload')
    target.write_bytes(b'payload')
    peer = tmp_path / ('0' * 32 + '.link')
    peer.write_bytes(b'payload' if same_bytes else b'peer')
    before = {p: p.read_bytes() for p in [source, target, peer]}
    monkeypatch.setattr(shared.uuid, 'uuid4', lambda: SimpleNamespace(hex='0' * 32))
    publish = shared.link_input
    depth = []

    def bounded(*args, **kwargs):
        depth.append(True)
        if len(depth) > 3:
            raise RecursionError('bounded recursive staging collision')
        return publish(*args, **kwargs)
    monkeypatch.setattr(shared, 'link_input', bounded)
    with pytest.raises(FileExistsError):
        shared.link_input(source, target)
    assert {p: p.read_bytes() for p in before} == before

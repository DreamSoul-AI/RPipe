"""Real concurrent publication and Windows handle-denial boundaries."""
from concurrent.futures import ThreadPoolExecutor
import os
import threading

import pytest

from rpipe.structure.artifacts import atomic_write_text

pytestmark = [pytest.mark.unit, pytest.mark.content, pytest.mark.p1,
              pytest.mark.structure_layer, pytest.mark.module_artifact,
              pytest.mark.cost(cost_class='c2'),
              pytest.mark.result_type('categorical', detail='summary')]


@pytest.mark.parametrize('clock', ['real', 'frozen'])
def test_atomic_writers_publish_complete_payloads_and_keep_peer_stage(tmp_path, monkeypatch, clock):
    import rpipe.structure.artifacts as atomic

    if clock == 'frozen':
        from types import SimpleNamespace
        monkeypatch.setattr(atomic, 'time', SimpleNamespace(time_ns=lambda: 123, sleep=atomic.time.sleep))
    target = tmp_path / 'result.txt'
    target.write_text('old', encoding='utf-8')
    peer = tmp_path / 'result.txt.peer.tmp'
    peer.write_text('another writer', encoding='utf-8')
    barrier, staged = threading.Barrier(2), []
    replace = atomic.atomic_replace
    def publish(source, destination):
        staged.append(source)
        barrier.wait(timeout=10)  # Both complete temporary files exist before either replace.
        return replace(source, destination)
    monkeypatch.setattr(atomic, 'atomic_replace', publish)
    payloads = ['A' * 4096, 'B' * 4096]
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(atomic_write_text, target, payload) for payload in payloads]
        assert [future.result(timeout=15) for future in futures] == [target, target]
    assert len(set(staged)) == 2 and all(not path.exists() for path in staged)
    assert target.read_text(encoding='utf-8') in payloads
    assert peer.read_text(encoding='utf-8') == 'another writer'
    assert set(tmp_path.iterdir()) == {target, peer}


@pytest.mark.skipif(os.name != 'nt', reason='requires real Windows file sharing semantics')
def test_windows_held_unicode_target_preserves_old_bytes_then_recovers(tmp_path):
    import ctypes
    from ctypes import wintypes

    directory = tmp_path / '权限 路径'
    directory.mkdir()
    target = directory / 'result.json'
    target.write_bytes(b'old evidence')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(target), 0x80000000, 1, None, 3, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        with pytest.raises(PermissionError) as caught:
            atomic_write_text(target, 'new evidence')
        assert caught.value.winerror in (5, 32, 33)
        assert target.read_bytes() == b'old evidence'
        assert set(directory.iterdir()) == {target}
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    assert atomic_write_text(target, 'new evidence') == target
    assert target.read_bytes() == b'new evidence'

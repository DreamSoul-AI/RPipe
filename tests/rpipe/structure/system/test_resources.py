"""Owned process-tree cleanup and output-accounting boundary checks."""

import ctypes
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from rpipe.structure.system import resources

pytestmark = [pytest.mark.content, pytest.mark.p1, pytest.mark.structure_layer,
              pytest.mark.module_system, pytest.mark.cost(cost_class='c1'),
              pytest.mark.result_type('categorical', detail='summary')]
windows = pytest.mark.skipif(os.name != 'nt', reason='Windows Job contract')


@pytest.mark.unit
def test_file_errors_reject_unsupported_platform(monkeypatch):
    monkeypatch.setattr(resources, 'os', SimpleNamespace(name='posix'))
    with pytest.raises(OSError, match='require Windows'):
        resources.WindowsFileErrors()


@windows
@pytest.mark.unit
def test_file_errors_use_crt_prototypes_and_do_not_return_stale_failed_reads(monkeypatch):
    state = SimpleNamespace(clear_status=0, read_status=0)
    def clear(value):
        assert value == 0
        return state.clear_status
    def read(target):
        target._obj.value = 32
        return state.read_status
    crt = SimpleNamespace(_set_doserrno=clear, _get_doserrno=read)
    def load(name):
        assert name == 'ucrtbase'
        return crt
    monkeypatch.setattr(resources.ctypes, 'CDLL', load)
    observer = resources.WindowsFileErrors()
    assert clear.argtypes == [ctypes.c_ulong] and clear.restype is ctypes.c_int
    assert read.argtypes == [ctypes.POINTER(ctypes.c_ulong)] and read.restype is ctypes.c_int
    assert observer.clear() and observer.code() == 32
    state.clear_status = state.read_status = 1
    assert observer.clear() is False and observer.code() is None


@pytest.mark.unit
def test_output_growth_counts_new_and_positive_bytes_without_double_counting(tmp_path):
    shared, output = tmp_path / 'shared', tmp_path / 'output'
    shared.mkdir()
    raw = shared / 'raw'
    raw.write_bytes(b'x' * 1024)
    baseline = resources.output_snapshot([shared, output])
    output.mkdir()
    os.link(raw, output / 'reference')
    raw.write_bytes(b'x' * 1536)
    fresh = output / 'fresh'
    fresh.write_bytes(b'y' * 2048)
    os.link(fresh, output / 'fresh-alias')
    assert resources.output_growth([output, shared, output], baseline) == 2560
    raw.write_bytes(b'x')
    assert resources.output_growth([output, shared], baseline) == 2048
    assert len(resources.output_snapshot([output, shared])) == 2


@pytest.mark.unit
def test_output_growth_retains_baseline_after_delete_and_counts_replacement(tmp_path):
    old = tmp_path / 'old'
    old.write_bytes(b'x' * 1024)
    baseline = resources.output_snapshot([tmp_path])
    old.rename(tmp_path / 'old-renamed')
    old.write_bytes(b'y' * 512)
    assert resources.output_growth([tmp_path], baseline) == 512
    assert baseline == resources.output_snapshot([tmp_path / 'old-renamed'])


@pytest.mark.unit
def test_output_observation_error_is_not_zero_bytes(tmp_path, monkeypatch):
    def denied(path):
        raise PermissionError('observer cannot read output')
    monkeypatch.setattr(resources.os, 'scandir', denied)
    with pytest.raises(PermissionError, match='observer cannot read'):
        resources.output_growth([tmp_path], {})


@pytest.mark.unit
def test_output_directory_links_fail_observation_without_traversing(tmp_path):
    output, outside = tmp_path / 'output', tmp_path / 'outside'
    output.mkdir()
    outside.mkdir()
    (outside / 'input').write_bytes(b'x' * 4096)
    link = output / 'linked'
    if os.name == 'nt':
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)],
                       capture_output=True, check=True, timeout=5)
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        for root in (output, link, link / 'input'):
            with pytest.raises(OSError, match='output (link|reparse)'):
                resources.output_snapshot([root])
    finally:
        if os.name == 'nt':
            link.rmdir()
        else:
            link.unlink()
    assert (outside / 'input').stat().st_size == 4096


@pytest.mark.unit
def test_output_file_links_reference_baseline_but_new_identity_counts(tmp_path):
    output, outside = tmp_path / 'output', tmp_path / 'outside'
    output.mkdir()
    outside.mkdir()
    known, unseen = outside / 'known', outside / 'unseen'
    known.write_bytes(b'x' * 1024)
    unseen.write_bytes(b'y' * 2048)
    (output / 'input').symlink_to(known)
    baseline = resources.output_snapshot([output])
    (output / 'input-alias').symlink_to(known)
    (output / 'new-reference').symlink_to(unseen)
    (output / 'new-alias').symlink_to(unseen)
    assert resources.output_growth([output], baseline) == 2048
    known.write_bytes(b'x' * 1536)
    assert resources.output_growth([output], baseline) == 2560


@pytest.mark.unit
def test_output_broken_file_link_is_observer_failure(tmp_path):
    (tmp_path / 'broken').symlink_to(tmp_path / 'absent')
    with pytest.raises(FileNotFoundError):
        resources.output_snapshot([tmp_path])


@pytest.mark.unit
def test_job_platform_fails_closed(monkeypatch):
    monkeypatch.setattr(resources.os, 'name', 'posix')
    with pytest.raises(OSError, match='requires Windows'):
        resources.WindowsJob()


def _fake_job(monkeypatch, pids=(101, 102)):
    job = object.__new__(resources.WindowsJob)
    job.handle = 1
    closed = []
    def membership(handle, job_handle, target):
        target._obj.value = True
        return True
    def memory(handle, target, size):
        target._obj.working_set = handle * 1024
        return True
    job.kernel = SimpleNamespace(OpenProcess=lambda *args: args[-1],
                                 IsProcessInJob=membership,
                                 CloseHandle=lambda handle: closed.append(handle) or True)
    job.psapi = SimpleNamespace(GetProcessMemoryInfo=memory)
    monkeypatch.setattr(job, 'pids', lambda: pids)
    return job, closed


@pytest.mark.unit
def test_rss_sums_job_members_and_closes_sample_handles(monkeypatch):
    job, closed = _fake_job(monkeypatch)
    assert job.rss_bytes() == (101 + 102) * 1024
    assert closed == [101, 102]


@windows
@pytest.mark.unit
def test_rss_observer_and_close_errors_keep_first_failure(monkeypatch):
    job, _ = _fake_job(monkeypatch)
    job.psapi.GetProcessMemoryInfo = lambda *args: False
    job.kernel.CloseHandle = lambda handle: False
    ctypes.set_last_error(5)
    with pytest.raises(OSError) as caught:
        job.rss_bytes()
    assert caught.value.winerror == 5
    assert len(caught.value.cleanup_errors) == 1
    assert caught.value.cleanup_errors[0].winerror == 5


@windows
@pytest.mark.unit
@pytest.mark.parametrize('gone', [False, True])
def test_rss_open_failure_only_ignores_confirmed_exited_member(monkeypatch, gone):
    job, _ = _fake_job(monkeypatch, (101,))
    values = iter([(101,), () if gone else (101,)])
    monkeypatch.setattr(job, 'pids', lambda: next(values))
    job.kernel.OpenProcess = lambda *args: None
    ctypes.set_last_error(5)
    if gone:
        assert job.rss_bytes() == 0
    else:
        with pytest.raises(OSError):
            job.rss_bytes()


@pytest.mark.unit
@pytest.mark.parametrize('gone', [False, True])
def test_rss_rechecks_reused_pid_membership_before_counting(monkeypatch, gone):
    job, closed = _fake_job(monkeypatch, (101,))
    values = iter([(101,), () if gone else (101,)])
    monkeypatch.setattr(job, 'pids', lambda: next(values))
    job.kernel.IsProcessInJob = lambda *args: True  # BOOL output remains false.
    job.psapi.GetProcessMemoryInfo = lambda *args: pytest.fail('foreign handle sampled')
    if gone:
        assert job.rss_bytes() == 0
    else:
        with pytest.raises(RuntimeError, match='membership changed'):
            job.rss_bytes()
    assert closed == [101]


@windows
@pytest.mark.unit
def test_job_membership_grows_buffer_and_exposes_query_failure():
    job = object.__new__(resources.WindowsJob)
    job.handle = 1
    def members(handle, kind, target, size, needed):
        value = target._obj
        value.assigned = 20
        if len(value.ids) < 20:
            ctypes.set_last_error(234)
            return False
        value.count = 20
        value.ids[:20] = range(100, 120)
        return True
    job.kernel = SimpleNamespace(QueryInformationJobObject=members)
    assert job.pids() == tuple(range(100, 120))
    job.kernel.QueryInformationJobObject = lambda *args: False
    ctypes.set_last_error(5)
    with pytest.raises(OSError):
        job.pids()


@windows
@pytest.mark.unit
def test_job_terminate_and_close_errors_remain_visible():
    job = object.__new__(resources.WindowsJob)
    job.handle = 1
    job.kernel = SimpleNamespace(TerminateJobObject=lambda *args: False,
                                 CloseHandle=lambda *args: False)
    ctypes.set_last_error(5)
    with pytest.raises(OSError):
        job.terminate()
    with pytest.raises(OSError):
        job.close()
    assert job.handle == 1
    job.kernel.CloseHandle = lambda *args: True
    job.close()
    job.close()
    with pytest.raises(RuntimeError, match='closed'):
        job.pids()


@windows
@pytest.mark.unit
def test_job_acquisition_preserves_set_limit_and_close_failures(monkeypatch):
    names = ('CreateJobObjectW', 'SetInformationJobObject', 'AssignProcessToJobObject',
             'QueryInformationJobObject', 'TerminateJobObject', 'OpenProcess',
             'IsProcessInJob', 'CloseHandle', 'GetProcessMemoryInfo')
    kernel = SimpleNamespace(**{name: (lambda *args: False) for name in names})
    kernel.CreateJobObjectW = lambda *args: 1
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: kernel)
    ctypes.set_last_error(5)
    with pytest.raises(OSError) as caught:
        resources.WindowsJob()
    assert caught.value.winerror == 5
    assert len(caught.value.cleanup_errors) == 1


@windows
@pytest.mark.integration
@pytest.mark.parametrize('close_only', [False, True], ids=['terminate', 'kill-on-close'])
def test_job_contains_orphan_grandchild_without_touching_unrelated_peer(tmp_path, close_only):
    marker = tmp_path / 'child-pid'
    worker = '''import os, subprocess, sys
from pathlib import Path
if not sys.stdin.readline():
    raise SystemExit(0)
child = subprocess.Popen([sys.executable, '-c', 'import time; block = bytearray(4 * 1024 * 1024); time.sleep(20)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
Path(sys.argv[1]).write_text(str(child.pid))
os._exit(0)
'''
    peer = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    job = resources.WindowsJob()
    child = None
    wait_handle = None
    try:
        child = subprocess.Popen([sys.executable, '-c', worker, str(marker)], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=0x08000000)
        job.assign(child)
        assert not marker.exists()
        assert child.pid in job.pids()
        child.stdin.write(b'go\n')
        child.stdin.close()
        child.wait(timeout=5)
        grandchild = int(marker.read_text())
        assert grandchild in job.pids()
        assert peer.pid not in job.pids()
        assert job.rss_bytes() > 0
        assert job.wait_empty(0.01) is False
        wait_handle = job.kernel.OpenProcess(0x100000, False, grandchild)
        assert wait_handle
        if close_only:
            job.close()
        else:
            job.terminate()
            assert job.wait_empty(3)
        wait = job.kernel.WaitForSingleObject
        wait.argtypes, wait.restype = [ctypes.c_void_p, ctypes.c_ulong], ctypes.c_ulong
        assert wait(wait_handle, 3000) == 0
        assert peer.poll() is None
    finally:
        if job.handle:
            job.terminate()
            assert job.wait_empty(3)
            job.close()
        if wait_handle:
            job.kernel.CloseHandle(wait_handle)
        if child is not None:
            if child.stdin and not child.stdin.closed:
                child.stdin.close()
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        peer.kill()
        peer.wait(timeout=5)

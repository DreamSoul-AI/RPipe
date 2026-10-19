"""Private process ownership for tests/run.py (Windows Job or POSIX session)."""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import time
from pathlib import Path


class _Limits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                ('flags', ctypes.c_uint32), ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t),
                ('process_limit', ctypes.c_uint32), ('affinity', ctypes.c_size_t),
                ('priority', ctypes.c_uint32), ('scheduling', ctypes.c_uint32)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', _Limits), ('io', ctypes.c_uint64 * 6), ('memory', ctypes.c_size_t * 4)]


class _Accounting(ctypes.Structure):
    _fields_ = [('times', ctypes.c_int64 * 4), ('faults', ctypes.c_uint32),
                ('total', ctypes.c_uint32), ('active', ctypes.c_uint32), ('terminated', ctypes.c_uint32)]


class _Thread(ctypes.Structure):
    _fields_ = [('size', ctypes.c_uint32), ('usage', ctypes.c_uint32), ('tid', ctypes.c_uint32),
                ('pid', ctypes.c_uint32), ('base_priority', ctypes.c_int32),
                ('delta_priority', ctypes.c_int32), ('flags', ctypes.c_uint32)]


class _WindowsJob:
    def __init__(self):
        self.handle = None
        self.cleanup_errors = []
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        handle, dword, pointer, boolean = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int
        signatures = {
            'CreateJobObjectW': ([pointer, ctypes.c_wchar_p], handle),
            'SetInformationJobObject': ([handle, ctypes.c_int, pointer, dword], boolean),
            'AssignProcessToJobObject': ([handle, handle], boolean),
            'QueryInformationJobObject': ([handle, ctypes.c_int, pointer, dword, pointer], boolean),
            'TerminateJobObject': ([handle, dword], boolean),
            'CloseHandle': ([handle], boolean),
            'CreateToolhelp32Snapshot': ([dword, dword], handle),
            'Thread32First': ([handle, ctypes.POINTER(_Thread)], boolean),
            'Thread32Next': ([handle, ctypes.POINTER(_Thread)], boolean),
            'OpenThread': ([dword, boolean, dword], handle),
            'ResumeThread': ([handle], dword),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result

    def _check(self, result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        return result

    def create(self):
        self.handle = self._check(self.api.CreateJobObjectW(None, None))
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
        self._check(self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))

    def _close_temporary(self, handle):
        if not self.api.CloseHandle(handle):
            self.cleanup_errors.append({'phase': 'temporary-handle',
                                        'error': repr(ctypes.WinError(ctypes.get_last_error()))})

    def assign_and_resume(self, proc):
        self._check(self.api.AssignProcessToJobObject(self.handle, int(proc._handle)))
        snapshot = self.api.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD.
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        thread = _Thread()
        thread.size = ctypes.sizeof(thread)
        ids = []
        try:
            exists = self.api.Thread32First(snapshot, ctypes.byref(thread))
            while exists:
                if thread.pid == proc.pid:
                    ids.append(thread.tid)
                thread.size = ctypes.sizeof(thread)
                exists = self.api.Thread32Next(snapshot, ctypes.byref(thread))
            if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES.
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._close_temporary(snapshot)
        if len(ids) != 1:
            raise RuntimeError(f'Expected one initial suspended thread, found {len(ids)}')
        handle = self._check(self.api.OpenThread(2, False, ids[0]))  # THREAD_SUSPEND_RESUME.
        try:
            previous = self.api.ResumeThread(handle)
            if previous == 0xffffffff:
                raise ctypes.WinError(ctypes.get_last_error())
            if previous != 1:
                raise RuntimeError(f'Unexpected initial thread suspend count: {previous}')
        finally:
            self._close_temporary(handle)

    def active(self):
        counts = _Accounting()
        self._check(self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(counts),
                                                      ctypes.sizeof(counts), None))
        return counts.active

    def terminate(self):
        self._check(self.api.TerminateJobObject(self.handle, 1))

    def close(self):
        if self.handle is not None:
            self._check(self.api.CloseHandle(self.handle))
            self.handle = None


class ProcessTree:
    def __init__(self):
        self.proc = None
        self.job = None

    def start(self, command, **kwargs):
        if os.name == 'nt':
            self.job = _WindowsJob()
            self.job.create()
            self.proc = subprocess.Popen(command, creationflags=4, **kwargs)  # CREATE_SUSPENDED.
            self.job.assign_and_resume(self.proc)
        else:
            self.proc = subprocess.Popen(command, start_new_session=True, **kwargs)
        return self.proc

    def wait(self, timeout):
        if timeout <= 0:
            raise subprocess.TimeoutExpired(self.proc.args, timeout)
        if os.name == 'nt':
            return self.proc.wait(timeout=timeout)
        deadline = time.monotonic() + timeout
        while True:
            result = os.waitid(os.P_PID, self.proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if result is not None:
                return result.si_status if result.si_code == os.CLD_EXITED else -result.si_status
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.proc.args, timeout)
            time.sleep(min(.01, max(0, deadline - time.monotonic())))

    def _active_group(self):
        if Path('/proc').is_dir():
            count = 0
            for entry in Path('/proc').iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                except (FileNotFoundError, ProcessLookupError):
                    continue
                if int(fields[2]) == self.proc.pid and fields[0] not in {'Z', 'X'}:
                    count += 1
            return count
        try:
            os.killpg(self.proc.pid, 0)
        except ProcessLookupError:
            return 0
        return 1

    def terminate(self, timeout=10):
        if self.proc is None:
            return 0
        active = self.job.active if self.job is not None else self._active_group
        before = active()
        if self.job is not None:
            if before:
                self.job.terminate()
        else:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + timeout
        while active():
            if time.monotonic() >= deadline:
                raise TimeoutError('Owned process tree termination not confirmed')
            time.sleep(.01)
        return before

    def close(self):
        if self.job is not None:
            self.job.close()

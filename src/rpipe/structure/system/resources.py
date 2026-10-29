"""Owned Windows process trees and observed output growth; no scientific imports."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import stat
import time


class WindowsFileErrors:
    """Capture the observing thread's Win32 file error retained by the CRT."""

    def __init__(self):
        if os.name != 'nt':
            raise OSError('Windows CRT file errors require Windows')
        self.crt = ctypes.CDLL('ucrtbase')
        self.crt._set_doserrno.argtypes = [ctypes.c_ulong]
        self.crt._set_doserrno.restype = ctypes.c_int
        self.crt._get_doserrno.argtypes = [ctypes.POINTER(ctypes.c_ulong)]
        self.crt._get_doserrno.restype = ctypes.c_int
        self._value = ctypes.c_ulong()

    def clear(self) -> bool:
        return self.crt._set_doserrno(0) == 0

    def code(self) -> int | None:
        return self._value.value if self.crt._get_doserrno(ctypes.byref(self._value)) == 0 else None


class _BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                ('flags', wintypes.DWORD), ('minimum_ws', ctypes.c_size_t),
                ('maximum_ws', ctypes.c_size_t), ('active', wintypes.DWORD),
                ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                ('scheduling', wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in
                ('read_ops', 'write_ops', 'other_ops', 'read', 'write', 'other')]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', _BasicLimits), ('io', _IoCounters),
                ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]


class _MemoryCounters(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('page_faults', wintypes.DWORD),
                *[(name, ctypes.c_size_t) for name in
                  ('peak_ws', 'working_set', 'peak_paged', 'paged', 'peak_nonpaged',
                   'nonpaged', 'pagefile', 'peak_pagefile')]]


class WindowsJob:
    """Anonymous kill-on-close Job; RSS is sampled, not a commit-memory limit."""

    def __init__(self):
        if os.name != 'nt':
            raise OSError('Windows Job supervision requires Windows')
        # Adapted from cpu-memory-cleanup 17c5363f06b548a63a47b98efcd118476cfedcb7
        # scripts/run-tests.py: anonymous Job ownership; only kill-on-close retained.
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.psapi = ctypes.WinDLL('psapi', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            'SetInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            'QueryInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            'TerminateJobObject': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            'OpenProcess': ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            'IsProcessInJob': ([wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result
        self.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        self.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException as primary:
            try:
                self.close()
            except BaseException as cleanup:
                primary.cleanup_errors = (*getattr(primary, 'cleanup_errors', ()), cleanup)
            raise

    def _require_open(self):
        if not self.handle:
            raise RuntimeError('Windows Job is closed')

    def assign(self, process):
        """Assign a still-gated Popen child before allowing its work to start."""
        self._require_open()
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def pids(self) -> tuple[int, ...]:
        self._require_open()
        capacity = 16
        for _ in range(8):
            class Members(ctypes.Structure):
                _fields_ = [('assigned', wintypes.DWORD), ('count', wintypes.DWORD),
                            ('ids', ctypes.c_size_t * capacity)]

            members = Members()
            if self.kernel.QueryInformationJobObject(self.handle, 3, ctypes.byref(members), ctypes.sizeof(members), None):
                if members.count > capacity:
                    raise RuntimeError('Windows Job returned an incomplete process list')
                return tuple(sorted(int(pid) for pid in members.ids[:members.count]))
            error = ctypes.get_last_error()
            if error != 234:  # ERROR_MORE_DATA during process-tree growth
                raise ctypes.WinError(error)
            capacity = max(capacity * 2, members.assigned)
        raise RuntimeError('Windows Job process list kept growing during observation')

    def rss_bytes(self) -> int:
        """Sum current working sets of Job members, checking membership after open."""
        total = 0
        for pid in self.pids():
            handle = self.kernel.OpenProcess(0x410, False, pid)  # QUERY_INFORMATION | VM_READ
            if not handle:
                error = ctypes.WinError(ctypes.get_last_error())
                if pid not in self.pids():
                    continue
                raise error
            primary = None
            try:
                member = wintypes.BOOL()
                if not self.kernel.IsProcessInJob(handle, self.handle, ctypes.byref(member)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if not member.value:
                    if pid in self.pids():
                        raise RuntimeError('Windows Job membership changed during observation')
                    continue
                value = _MemoryCounters()
                value.cb = ctypes.sizeof(value)
                if not self.psapi.GetProcessMemoryInfo(handle, ctypes.byref(value), value.cb):
                    error = ctypes.WinError(ctypes.get_last_error())
                    if pid not in self.pids():
                        continue
                    raise error
                total += value.working_set
            except BaseException as error:
                primary = error
                raise
            finally:
                if not self.kernel.CloseHandle(handle):
                    cleanup = ctypes.WinError(ctypes.get_last_error())
                    if primary is None:
                        raise cleanup
                    primary.cleanup_errors = (*getattr(primary, 'cleanup_errors', ()), cleanup)
        return total

    def terminate(self):
        self._require_open()
        if not self.kernel.TerminateJobObject(self.handle, 124):
            raise ctypes.WinError(ctypes.get_last_error())

    def wait_empty(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while self.pids():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.02, remaining))
        return True

    def close(self):
        if self.handle:
            if not self.kernel.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


def _output_info(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode):
        # Input references may be file symlinks; only inspect their target metadata.
        target = path.stat()
        if not stat.S_ISREG(target.st_mode):
            raise OSError(f'directory or unsupported output link: {path}')
        return target
    if getattr(info, 'st_file_attributes', 0) & 0x400:
        raise OSError(f'unsupported output reparse point: {path}')
    return info


def output_snapshot(roots) -> dict[tuple[int, int], int]:
    """Count file identities once; reject directory links and inspect file links."""
    files, directories = {}, set()
    pending = []
    for root in roots:
        path = Path(root).absolute()
        for ancestor in (path, *path.parents):
            _output_info(ancestor)
        pending.append(path)
    while pending:
        path = pending.pop()
        info = _output_info(path)
        if info is None:
            continue
        identity = (info.st_dev, info.st_ino)
        if not info.st_ino:
            raise OSError(f'output file identity unavailable: {path}')
        if stat.S_ISDIR(info.st_mode):
            if identity not in directories:
                directories.add(identity)
                try:
                    with os.scandir(path) as entries:
                        pending.extend(Path(entry.path) for entry in entries)
                except FileNotFoundError:
                    continue
        elif stat.S_ISREG(info.st_mode):
            files[identity] = max(files.get(identity, 0), info.st_size)
    return files


def output_growth(roots, baseline: dict[tuple[int, int], int]) -> int:
    """Observe new bytes plus positive growth, without charging old inputs twice."""
    return sum(max(0, size - baseline.get(identity, 0))
               for identity, size in output_snapshot(roots).items())

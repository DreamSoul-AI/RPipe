"""Bounded UTF-8 diagnostic JSONL; failures never replace scientific facts.

One 8 KiB record at a time, no queue; 256 KiB per segment, three backups,
and one first-error record. Seven-day retention runs at open, emit and close;
the caller owns expiry maintenance while no RPipe process is running.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from itertools import islice
import multiprocessing
import os
from pathlib import Path
import re
import time
from typing import Any
import unicodedata
import uuid

from rpipe import __version__
from rpipe.structure.artifact.io import _owned_stream, cache_lock

_RUNTIME_ID = uuid.uuid4().hex
_TOKEN = re.compile(r'^\w[\w.:-]{0,127}$')
_EVENT = re.compile(r'^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$')
_PRIVATE = re.compile(
    r'(?i)\b(password|passwd|token|authorization|api[_-]?key|secret|credential|'
    r'input|prompt|content|document|reasoning|path|email|value)\s*[=:]\s*'
    r'[\s\S]*')
_PATH = re.compile(r'(?i)(?:[a-z]:[\\/]|(?<!\w)/)[^\s;,]*')
_EMAIL = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')
_URL = re.compile(r'(?i)\b(?:https?|ftp)://[^\s]+')
_CONTEXT = frozenset({'runtime_id', 'task_id', 'workflow_id', 'request_id', 'attempt_id',
                      'task_type', 'layer', 'phase', 'status', 'operation', 'code',
                      'sink', 'drop_reason', 'count_scope', 'outcome', 'exception_type'})
_NUMBERS = frozenset({'duration_ms', 'dropped_count', 'dropped_bytes', 'content_size_bytes',
                      'input_tokens', 'output_tokens', 'attempt', 'max_attempts', 'retry_in_ms'})
_REPORT = frozenset({'epoch', 'step', 'lr', 'elapsed', 'eta', 'test_accuracy', 'best_accuracy',
                    'best_value', 'best_metric', 'improved', 'stall', 'epoch_complete',
                    'train_loss', 'train_accuracy', 'test_loss'})


def _text(value: Any, limit: int) -> tuple[str, bool]:
    # Only declared text reaches diagnostics; never repr an arbitrary business object.
    value = value if isinstance(value, str) else '[omitted]'
    if len(value) > 16_384:
        return '[omitted oversized diagnostic]', True
    value = _PRIVATE.sub(lambda match: match[1] + '=[omitted]', value)
    value = _URL.sub('[omitted]', value)
    value = _PATH.sub('[omitted]', value)
    value = _EMAIL.sub('[omitted]', value)
    value = ''.join(char if not unicodedata.category(char).startswith('C') and char not in '\u2028\u2029'
                    else '\\u%04x' % ord(char) for char in value)
    raw = value.encode('utf-8', errors='backslashreplace')
    return raw[:limit].decode('utf-8', errors='ignore'), len(raw) > limit


def _encoded(record: dict) -> bytes:
    return (json.dumps(record, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


class Logger:
    MAX_RECORD_BYTES = 8192
    MAX_FILE_BYTES = 256 * 1024
    BACKUPS = 3
    RETENTION_SECONDS = 7 * 24 * 60 * 60

    def __init__(self, assets_dir: Path | str, *, logger: str = 'rpipe',
                 debug: bool = False, stdout: bool = True, **context: Any) -> None:
        self.assets_dir = Path(assets_dir)
        self.path = self.assets_dir / 'logs' / 'events.jsonl'
        self.name = logger if _TOKEN.fullmatch(logger) else 'rpipe'
        self.debug_enabled, self.stdout = debug, stdout
        self.context = {key: value for key, value in context.items() if key in _CONTEXT | _NUMBERS | {'retryable'}}
        self.run_id = (self.assets_dir.parent.name
                       if self.assets_dir.name == 'assets' and self.assets_dir.parent.parent.name == 'runs'
                       else None)
        self.context['runtime_id'] = self.context.get('runtime_id') or _RUNTIME_ID
        self.diagnostics_complete = True
        self._failed: set[str] = set()
        try:
            with cache_lock(self.path.parent, timeout=0):
                self._expire()
                with _owned_stream(closing(self.path.open('ab'))):
                    pass
        except Exception:
            self.diagnostics_complete = False

    def bind(self, **context: Any) -> None:
        self.context.update({key: value for key, value in context.items()
                             if key in _CONTEXT | _NUMBERS | {'retryable'}})

    def _record(self, level: str, event: str, message: str, fields: dict) -> dict:
        truncated = []
        message, cut = _text(message, 2048)
        if cut:
            truncated.append('message')
        record = {'timestamp': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
                  'level': level, 'event': event if _EVENT.fullmatch(event) and len(event) <= 128 else 'logging.unstructured_record',
                  'logger': self.name, 'message': message, 'app_name': 'rpipe', 'app_version': __version__,
                  'runtime_id': _RUNTIME_ID, 'process_name': multiprocessing.current_process().name,
                  'pid': os.getpid()}
        allowed = _CONTEXT | _NUMBERS | {'retryable', 'traceback', 'exception_message', 'exit_code'}
        values = {**self.context, **{key: value for key, value in fields.items() if key in allowed}}
        if self.run_id is not None:
            values.setdefault('task_id', self.run_id)
        for key, value in values.items():
            if value is None:
                continue
            if key in _CONTEXT:
                if isinstance(value, str) and _TOKEN.fullmatch(value):
                    record[key] = value
                elif isinstance(value, str):
                    truncated.append(key)
            elif key in _NUMBERS and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                record[key] = value
            elif key == 'exit_code' and type(value) is int:
                record[key] = value
            elif key == 'retryable' and isinstance(value, bool):
                record[key] = value
            elif key in {'traceback', 'exception_message'}:
                safe, cut = _text(value, 4096 if key == 'traceback' else 1024)
                record[key] = safe
                if cut:
                    truncated.append(key)
        if truncated:
            record['truncated_fields'] = sorted(set(truncated))[:32]
        # Removing optional fields cannot turn an overlong identifier into a valid new one.
        for key in ('traceback', 'exception_message', *sorted(values)):
            if len(_encoded(record)) <= self.MAX_RECORD_BYTES:
                break
            if key in record and key not in {'runtime_id', 'message'}:
                del record[key]
                truncated.append(key)
                record['truncated_fields'] = sorted(set(truncated))[:32]
        return record

    def _managed_paths(self):
        return [self.path, self.path.with_name('events.first-error.jsonl'),
                *(self.path.with_name(f'events.{index}.jsonl') for index in range(1, self.BACKUPS + 1))]

    def _expire(self) -> None:
        cutoff = time.time() - self.RETENTION_SECONDS
        for path in self._managed_paths():
            if path.exists() and path.stat().st_mtime < cutoff:
                path.unlink()

    def _append(self, payload: bytes) -> None:
        with _owned_stream(closing(self.path.open('ab'))) as handle:
            handle.write(payload)
            handle.flush()

    def _write_file(self, record: dict) -> None:
        payload = _encoded(record)
        with cache_lock(self.path.parent, timeout=0):
            self._expire()
            # Age the active segment by its first record, so busy logging cannot extend retention.
            created = self.path.stat().st_mtime if self.path.exists() else time.time()
            size = self.path.stat().st_size if self.path.exists() else 0
            interrupted = False
            if size:
                with _owned_stream(closing(self.path.open('rb'))) as handle:
                    handle.seek(-1, os.SEEK_END)
                    interrupted = handle.read(1) != b'\n'
            if interrupted or size + len(payload) > self.MAX_FILE_BYTES:
                last = self.path.with_name(f'events.{self.BACKUPS}.jsonl')
                dropped_bytes, dropped_count = 0, 0
                if last.exists():
                    dropped_bytes = last.stat().st_size
                    with _owned_stream(closing(last.open('rb'))) as handle:
                        for chunk in iter(lambda: handle.read(8192), b''):
                            dropped_count += chunk.count(b'\n')
                    last.unlink()
                for index in range(self.BACKUPS - 1, -1, -1):
                    source = self.path if index == 0 else self.path.with_name(f'events.{index}.jsonl')
                    if source.exists():
                        source.replace(self.path.with_name(f'events.{index + 1}.jsonl'))
                created = time.time()
                if dropped_bytes:
                    self.diagnostics_complete = False
                    notice = self._record('WARNING', 'logging.records_dropped', 'Old log segment removed at capacity',
                                          {'sink': 'file', 'drop_reason': 'retention_capacity',
                                           'dropped_count': dropped_count, 'dropped_bytes': dropped_bytes,
                                           'count_scope': 'removed_segment'})
                    self._append(_encoded(notice))
                if interrupted:
                    self.diagnostics_complete = False
                    self._append(_encoded(self._record('WARNING', 'logging.unstructured_record',
                                                      'Previous interrupted record remains incomplete',
                                                      {'sink': 'file', 'code': 'interrupted_record'})))
            self._append(payload)
            os.utime(self.path, (created, created))
            if record['level'] in {'ERROR', 'CRITICAL'}:
                first = self.path.with_name('events.first-error.jsonl')
                try:
                    with _owned_stream(closing(first.open('xb'))) as handle:
                        handle.write(payload)
                        handle.flush()
                except FileExistsError:
                    pass

    def _send(self, record: dict, sinks: set[str]) -> set[str]:
        healthy = set()
        for sink in ('file', 'stdout'):
            if sink not in sinks:
                continue
            try:
                if sink == 'file':
                    self._write_file(record)
                else:
                    print(_encoded(record).decode('utf-8').rstrip('\n'), flush=True)
                healthy.add(sink)
            except Exception:
                self.diagnostics_complete = False
        return healthy

    def _emit(self, level: str, event: str, message: str, **fields: Any) -> None:
        if level == 'DEBUG' and not self.debug_enabled:
            return
        try:
            record = self._record(level, event, message, fields)
            sinks = {'file', 'stdout'} if self.stdout else {'file'}
            healthy = self._send(record, sinks)
            failed = sinks - healthy
            for sink in sorted(failed - self._failed):
                self._send(self._record('WARNING', 'logging.sink_failed', 'Diagnostic output unavailable',
                                       {'sink': sink, 'drop_reason': 'delivery_unconfirmed',
                                        'count_scope': 'logger_instance'}), healthy)
            for sink in sorted(self._failed & healthy):
                self._send(self._record('INFO', 'logging.recovered', 'Diagnostic output recovered',
                                       {'sink': sink, 'count_scope': 'logger_instance'}), healthy)
            self._failed = failed
        except Exception:
            self.diagnostics_complete = False

    def debug(self, message: str, *, event: str = 'diagnostic.detail', **fields: Any) -> None:
        self._emit('DEBUG', event, message, **fields)

    def info(self, message: str, *, event: str = 'operation.observed', **fields: Any) -> None:
        aliases = {'ckpt': 'checkpoint.saved', 'resume': 'checkpoint.restored'}
        self._emit('INFO', aliases.get(event, event), message, **fields)

    def warning(self, message: str, *, event: str = 'operation.degraded', **fields: Any) -> None:
        self._emit('WARNING', event, message, **fields)

    def error(self, message: str, *, event: str = 'operation.failed', **fields: Any) -> None:
        self._emit('ERROR', event, message, **fields)

    def critical(self, message: str, *, event: str = 'runtime.failed', **fields: Any) -> None:
        self._emit('CRITICAL', event, message, **fields)

    def exception(self, message: str, exc: BaseException | dict[str, Any], *, event: str = 'task.failed', **fields: Any) -> None:
        try:
            from rpipe.structure.artifact.io import safe_error_record
            error = safe_error_record(exc)
            chain = error.get('traceback', '')
            causes = list(error.get('causes', []))
            for _ in range(16):
                if not causes:
                    break
                cause = causes.pop(0)
                chain += '\n' + cause.get('type', '') + ': ' + cause.get('message', '') + '\n' + cause.get('traceback', '')
                causes.extend(cause.get('causes', []))
                if len(chain) > 4096:
                    break
            chain = chain[:4097]
            self._emit('ERROR', event, message, **{**fields, 'exception_type': error['type'],
                       'exception_message': error['message'], 'traceback': chain,
                       'exit_code': error.get('exit_code')})
        except Exception:
            self.diagnostics_complete = False
            self._emit('ERROR', event, message, **{**fields, 'exception_type': type(exc).__name__})

    def report(self, tracker: Any, split: str, extra: dict[str, Any] | None = None) -> None:
        try:
            if split not in {'train', 'test', 'validation', 'eval'}:
                split = 'other'
            extra = extra or {}
            parts = [str(extra['epoch'])] if isinstance(extra.get('epoch'), (int, float)) else []
            parts.append(f'[split] {split}')
            display = getattr(tracker, 'display_mean', None)
            means = display(split) if callable(display) else tracker.mean(split) if tracker is not None else {}
            lasts = tracker.last(split) if tracker is not None else {}
            metrics = []
            for name in sorted(set(islice(means, 32)) | set(islice(lasts, 32)))[:32]:
                value = means.get(name, lasts.get(name))
                if isinstance(name, str) and _TOKEN.fullmatch(name) and isinstance(value, (int, float)):
                    metrics.append(f'{name}={value:.4f}')
            if metrics:
                parts.append('[metric] ' + ' '.join(metrics))
            timings = [f'{key}={extra[key]}' for key in ('elapsed', 'eta')
                       if isinstance(extra.get(key), str) and re.fullmatch(r'[0-9:.]+', extra[key])]
            if timings:
                parts.append('[time] ' + ' '.join(timings))
            for key, value in extra.items():
                if key in _REPORT - {'epoch', 'elapsed', 'eta'} and (
                    isinstance(value, (int, float, bool)) or key == 'best_metric' and isinstance(value, str) and _TOKEN.fullmatch(value)):
                    parts.append(f'{key}={value}')
            self._emit('INFO', 'metrics.reported', ' '.join(parts))
        except Exception:
            self.diagnostics_complete = False

    def close(self) -> None:
        try:
            with cache_lock(self.path.parent, timeout=0):
                self._expire()
        except Exception:
            self.diagnostics_complete = False
            if self.stdout:
                self._send(self._record('WARNING', 'logging.sink_failed', 'Log retention cleanup unavailable',
                                        {'sink': 'file', 'operation': 'retention_cleanup',
                                         'drop_reason': 'cleanup_unconfirmed'}), {'stdout'})

    def state_dict(self) -> dict[str, Any]:
        return {'path': str(self.path)}

    def load_state_dict(self, state: dict[str, Any] | None) -> None:
        del state

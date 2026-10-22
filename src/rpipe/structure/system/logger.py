"""System Logger: stdout + assets/logs/run.log (flush on every emit).

One record per line::

    <RFC3339 ms+offset> <LEVEL> <run id or -> [<event>] <payload>

LEVEL is ``INFO``, ``WARN``, or ``ERROR``, padded to 5 characters.
"""

from __future__ import annotations

from contextlib import closing

import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from rpipe.structure.artifacts import _owned_stream
from rpipe.structure import artifacts as kinds

_LEVELS = {'INFO': 'INFO ', 'WARN': 'WARN ', 'ERROR': 'ERROR'}


class Logger:
    def __init__(self, assets_dir: Path | str) -> None:
        self.assets_dir = Path(assets_dir)
        parts = self.assets_dir.parts
        self.run_id = (
            parts[-2]
            if len(parts) >= 3 and parts[-1] == 'assets' and parts[-3] == 'runs'
            else None
        )
        self.path = self.assets_dir / kinds.RUN_LOG
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.is_file():
            with self.path.open('a', encoding='utf-8'):
                pass

    def _emit(self, level: str, event: str, message: str) -> None:
        first_error = None
        for message in f'{message}'.splitlines() or ['']:
            stamp = datetime.now().astimezone().isoformat(timespec='milliseconds')
            run_id = self.run_id or '-'
            text = f'{stamp} {_LEVELS[level]} {run_id} [{event}] {message}'.rstrip()
            try:
                print(text, flush=True)
            except (OSError, ValueError) as error:
                if first_error is None:
                    first_error = error
            try:
                separator = ''
                try:
                    with _owned_stream(self.path.open('rb')) as previous:
                        previous.seek(0, 2)
                        if previous.tell():
                            previous.seek(-1, 2)
                            if previous.read(1) not in (b'\n', b'\r'):
                                separator = '\n'
                except OSError:
                    pass  # shortcut: unreadable tails stay append-only; repair requires readable log bytes.
                with _owned_stream(closing(self.path.open('a', encoding='utf-8'))) as handle:
                    handle.write(separator + text + '\n')
                    handle.flush()
            except (OSError, ValueError) as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    def info(self, message: str, *, event: str = 'flow') -> None:
        self._emit('INFO', event, message)

    def warning(self, message: str) -> None:
        self._emit('WARN', 'warn', message)

    def error(self, message: str) -> None:
        self._emit('ERROR', 'error', message)

    def exception(self, message: str, exc: BaseException) -> None:
        """ERROR line plus traceback. Same text on stdout and ``run.log``."""
        first_error = None
        try:
            self.error(f'{message} {type(exc).__name__}: {exc}')
        except (OSError, ValueError) as error:
            first_error = error
        formatted = traceback.format_exception(type(exc), exc, exc.__traceback__)
        for line in ''.join(formatted).splitlines():
            if line:
                try:
                    self._emit('ERROR', 'error', line)
                except (OSError, ValueError) as error:
                    if first_error is None:
                        first_error = error
        if first_error is not None:
            raise first_error

    def report(self, tracker: Any, split: str, extra: dict[str, Any] | None = None) -> None:
        extra = extra or {}
        epoch = extra.get('epoch')
        event = 'epoch' if epoch is not None else 'split'
        parts: list[str] = []
        if epoch is not None:
            parts.append(str(epoch))
        parts.append(f'[split] {split}')
        means = {}
        lasts = {}
        if tracker is not None:
            display = getattr(tracker, 'display_mean', None)
            means = display(split) if callable(display) else tracker.mean(split)
            lasts = tracker.last(split)
        metrics: list[str] = []
        for name in sorted(set(means) | set(lasts)):
            mean = means.get(name)
            last = lasts.get(name)
            if mean is not None:
                metrics.append(f'{name}={mean:.4f}')
            elif last is not None:
                metrics.append(f'{name}={last:.4f}')
        if metrics:
            parts.append('[metric] ' + ' '.join(metrics))
        time_bits: list[str] = []
        for key in ('elapsed', 'eta'):
            if extra.get(key) is not None:
                time_bits.append(f'{key}={extra[key]}')
        if time_bits:
            parts.append('[time] ' + ' '.join(time_bits))
        skip = {'epoch', 'elapsed', 'eta'}
        for key, value in extra.items():
            if key in skip:
                continue
            parts.append(f'{key}={value}')
        self._emit('INFO', event, ' '.join(parts))

    def state_dict(self) -> dict[str, Any]:
        return {'path': str(self.path)}

    def load_state_dict(self, state: dict[str, Any] | None) -> None:
        del state

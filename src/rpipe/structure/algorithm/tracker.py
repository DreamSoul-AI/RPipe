"""AlgorithmTracker: numeric ledger (curves in assets/tracker)."""

from __future__ import annotations

from contextlib import closing

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from rpipe.structure import artifacts as kinds
from rpipe.structure.artifacts import _owned_stream
from rpipe.structure.artifacts import atomic_write_text
from rpipe.structure.algorithm.metrics import MetricBundle


class _Meter:
    __slots__ = ('last', 'weighted_sum', 'n', 'history')

    def __init__(self) -> None:
        self.last = 0.0
        self.weighted_sum = 0.0
        self.n = 0
        self.history: list[float] = []

    @property
    def mean(self) -> float:
        if self.n <= 0:
            return 0.0
        return self.weighted_sum / self.n

    def append(self, value: float, n: int) -> None:
        weight = max(int(n), 1)
        self.last = float(value)
        self.weighted_sum += float(value) * weight
        self.n += weight

    def save_point(self) -> float:
        point = self.mean
        self.history.append(point)
        return point

    def reset_running(self) -> None:
        self.weighted_sum = 0.0
        self.n = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            'last': self.last,
            'mean': self.mean,
            'weighted_sum': self.weighted_sum,
            'n': self.n,
            'history': list(self.history),
        }


class AlgorithmTracker:
    """Per-split running means; jsonl + tracker_state on flush. Not a Logger."""

    def __init__(self, assets_dir: Path | str, metrics: MetricBundle | None = None) -> None:
        self.assets_dir = Path(assets_dir)
        self.root = self.assets_dir / kinds.TRACKER
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.assets_dir / kinds.TRACKER_STATE
        self.jsonl_path = self.assets_dir / kinds.TRACKER_JSONL
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.jsonl_path.is_file():
            with self.jsonl_path.open('a', encoding='utf-8'):
                pass
        self.step = 0
        self.progress: dict[str, Any] | None = None
        self.metrics = metrics or MetricBundle()
        self._meters: dict[str, dict[str, _Meter]] = defaultdict(dict)
        self._last_segment: dict[str, dict[str, float]] = {}
        self._pending: dict[str, dict[str, float]] = {}
        self._segment_batches: dict[str, int] = defaultdict(int)

    def _meter(self, split: str, name: str) -> _Meter:
        split_map = self._meters[split]
        if name not in split_map:
            split_map[name] = _Meter()
        return split_map[name]

    def evaluate(
        self,
        split: str,
        mode: str = 'batch',
        input: Any = None,
        output: Any = None,
    ) -> dict[str, float]:
        if input is None or output is None:
            return {}
        values = self.metrics.evaluate(split, mode, input, output)
        self._pending[split] = values
        return values

    def append(
        self,
        split: str,
        n: int = 1,
        values: dict[str, float] | None = None,
    ) -> None:
        payload = values if values is not None else self._pending.get(split) or {}
        for name, value in payload.items():
            self._meter(split, name).append(value, n)
        self.step += 1
        self._segment_batches[split] += 1

    def has_samples(self, split: str) -> bool:
        return self._segment_batches.get(split, 0) > 0

    def mean(self, split: str) -> dict[str, float]:
        return {name: meter.mean for name, meter in self._meters.get(split, {}).items()}

    def display_mean(self, split: str) -> dict[str, float]:
        """Running mean if this segment still has samples; else last saved segment."""
        running = {name: meter.mean for name, meter in self._meters.get(split, {}).items() if meter.n > 0}
        if running:
            return running
        return self.segment_mean(split)

    def last(self, split: str) -> dict[str, float]:
        return {name: meter.last for name, meter in self._meters.get(split, {}).items()}

    def segment_mean(self, split: str) -> dict[str, float]:
        if split in self._last_segment:
            return dict(self._last_segment[split])
        return self.mean(split)

    def save(self, split: str | None = None) -> None:
        splits = [split] if split else list(self._meters)
        for name in splits:
            for metric, value in self.metrics.finish_full(name).items():
                self._meter(name, metric).append(value, 1)
            self._last_segment[name] = {
                metric: meter.save_point() for metric, meter in self._meters.get(name, {}).items()
            }

    def reset(self, split: str | None = None) -> None:
        splits = [split] if split else list(self._meters)
        for name in splits:
            self._segment_batches[name] = 0
            for meter in self._meters.get(name, {}).values():
                meter.reset_running()

    def begin_run(self, checkpoint_state: dict[str, Any] | None = None) -> None:
        """Keep raw diagnostics; delimit the checkpoint's committed curve prefix."""
        state = checkpoint_state or {}
        offset = state.get('jsonl_offset')
        keep = 0
        if (
            state.get('jsonl_path') == str(self.jsonl_path.resolve())
            and type(offset) is int and 0 <= offset <= self.jsonl_path.stat().st_size
        ):
            keep = offset
        with _owned_stream(closing(self.jsonl_path.open('ab+'))) as handle:
            if handle.tell():
                handle.seek(-1, 2)
                if handle.read(1) != b'\n':
                    handle.write(b'\n')  # Isolate a crash's partial record from the new marker.
            handle.write((json.dumps({'event': 'start', 'keep_until': keep}) + '\n').encode('utf-8'))

    def append_jsonl(self, split: str, *, progress: dict[str, Any] | None = None) -> None:
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        means = self.mean(split)
        with _owned_stream(closing(self.jsonl_path.open('a', encoding='utf-8'))) as handle:
            for name, value in means.items():
                row = {'step': self.step, 'split': split, 'name': name, 'mean': value}
                if progress is not None:
                    if progress.get('step') is not None:
                        row['optimizer_step'] = progress['step']
                    if progress.get('epoch') is not None:
                        row['epoch'] = progress['epoch']
                line = json.dumps(row, ensure_ascii=False)
                handle.write(line + '\n')
            handle.flush()

    def flush_state(self) -> None:
        body = self.state_dict()
        atomic_write_text(self.state_path, json.dumps(body, indent=2, ensure_ascii=False))

    def flush(self, split: str | None = None, *, progress: dict[str, Any] | None = None) -> None:
        if split:
            self.progress = {key: progress[key] for key in ('step', 'epoch') if key in progress} if progress is not None else None
            self.append_jsonl(split, progress=progress)
        self.flush_state()

    def state_dict(self) -> dict[str, Any]:
        return {
            'step': self.step,
            'progress': self.progress,
            'segment_batches': dict(self._segment_batches),
            'jsonl_path': str(self.jsonl_path.resolve()),
            'jsonl_offset': self.jsonl_path.stat().st_size,
            'splits': {
                split: {name: meter.as_dict() for name, meter in meters.items()}
                for split, meters in self._meters.items()
            },
            'last_segment': self._last_segment,
        }

    def load_state_dict(self, state: dict[str, Any] | None) -> None:
        if not state:
            return
        self.step = int(state.get('step') or 0)
        self.progress = state.get('progress')
        self._last_segment = dict(state.get('last_segment') or {})
        self._meters = defaultdict(dict)
        self._segment_batches = defaultdict(int, state.get('segment_batches') or {})
        for split, metrics in (state.get('splits') or {}).items():
            for name, body in (metrics or {}).items():
                meter = _Meter()
                meter.last = float(body.get('last') or 0.0)
                meter.n = int(body.get('n') or 0)
                mean = float(body.get('mean') or 0.0)
                meter.weighted_sum = float(body.get('weighted_sum', mean * meter.n))
                meter.history = list(body.get('history') or [])
                self._meters[split][name] = meter
                if 'segment_batches' not in state and meter.n > 0:
                    self._segment_batches[split] = 1


def make_tracker(assets_dir: Path | str, algorithm_config: Any = None) -> AlgorithmTracker:
    from rpipe.structure.algorithm.metrics import resolve_metric_names

    names = resolve_metric_names(algorithm_config.setting('metric')) if algorithm_config is not None else None
    glue = str(algorithm_config.setting('glue_subset', 'cola') or 'cola') if algorithm_config is not None else 'cola'
    return AlgorithmTracker(assets_dir, metrics=MetricBundle(names, glue_subset=glue))

"""Read sibling results and assemble Experiment aggregates (not a result rewrite)."""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

from rpipe.structure.api.artifact_api import _owned_stream, _read_text
from rpipe.structure.api.artifact_api import kinds
from rpipe.structure.api.artifact_api import CorruptArtifactError
from rpipe.structure.api.artifact_api import PROCESS_NAME, RESULT_NAME, RUNS_DIRNAME
from rpipe.structure.api.artifact_api import STATUS_SUCCEEDED, load_result


def process_path(study_dir: Path | str) -> Path:
    return Path(study_dir) / PROCESS_NAME


def run_process_path(run_root: Path | str) -> Path:
    return Path(run_root) / PROCESS_NAME


def _state_history(study_dir: Path | str, run_dir: str) -> dict[str, dict[str, list[float]]]:
    path = Path(study_dir) / RUNS_DIRNAME / run_dir / 'assets' / kinds.TRACKER_STATE
    try:
        if not path.is_file():
            return {}
        body = json.loads(_read_text(path))
    except (OSError, ValueError, RecursionError, UnicodeError, TypeError):
        return {}
    splits = body.get('splits') if isinstance(body, dict) else None
    if not isinstance(splits, dict):
        return {}
    out: dict[str, dict[str, list[float]]] = {}
    for split, meters in splits.items():
        if not isinstance(meters, dict):
            continue
        names: dict[str, list[float]] = {}
        for name, meter in meters.items():
            history = meter.get('history') if isinstance(meter, dict) else None
            if not isinstance(history, list):
                continue
            values = [
                float(value)
                for value in history
                if _finite(value)
            ]
            if values:
                names[str(name)] = values
        if names:
            out[str(split)] = names
    return out


def _finite(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def load_curve_series(study_dir: Path | str, run_dir: str) -> dict[str, dict[str, dict[str, Any]]]:
    """Read the final JSONL trajectory; old observations never acquire guessed steps."""
    out = _jsonl_curve_series(Path(study_dir) / RUNS_DIRNAME / run_dir / 'assets' / kinds.TRACKER_JSONL)
    for split, names in _state_history(study_dir, run_dir).items():
        for name, values in names.items():
            if name not in out.get(split, {}):
                out.setdefault(split, {})[name] = {
                    'unit': 'observation', 'x': list(range(1, len(values) + 1)), 'values': values,
                }
    return out


def _jsonl_curve_series(path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    # ponytail: read one log in memory; index byte offsets if very long logs become a bottleneck.
    records = []
    try:
        with _owned_stream(path.open('rb')) as handle:
            while line := handle.readline():
                end = handle.tell()
                try:
                    row = json.loads(line)
                except (ValueError, RecursionError, UnicodeDecodeError):
                    continue
                if isinstance(row, dict):
                    records.append((end - len(line), end, row))
    except OSError:
        pass
    # Walk pointers backwards, so even a later restore of an older prefix is exact.
    active = []
    limit = math.inf
    for start, end, row in reversed(records):
        if end > limit:
            continue
        if row.get('event') == 'start':
            keep = row.get('keep_until')
            if type(keep) is int and 0 <= keep <= start:
                limit = keep
            continue
        active.append(row)
    buckets: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in reversed(active):
        split, name = row.get('split'), row.get('name')
        if isinstance(split, str) and isinstance(name, str) and _finite(row.get('mean')):
            buckets.setdefault(split, {}).setdefault(name, []).append(row)
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for split, names in buckets.items():
        for name, rows in names.items():
            if all(type(row.get('optimizer_step')) is int and row['optimizer_step'] >= 0 for row in rows):
                unit, key = 'step', 'optimizer_step'
            elif all('optimizer_step' not in row and _finite(row.get('epoch')) and row['epoch'] >= 0 for row in rows):
                unit, key = 'epoch', 'epoch'
            else:
                unit, key = 'observation', None
            if key is None:
                x, values = list(range(1, len(rows) + 1)), [row['mean'] for row in rows]
            else:
                points = {row[key]: row['mean'] for row in rows}
                x = sorted(points)
                values = [points[point] for point in x]
            out.setdefault(split, {})[name] = {'unit': unit, 'x': x, 'values': values}
    return out


def load_tracker_history(study_dir: Path | str, run_dir: str) -> dict[str, dict[str, list[float]]]:
    return {
        split: {name: series['values'] for name, series in names.items()}
        for split, names in load_curve_series(study_dir, run_dir).items()
    }


def summarize_curve_series(series: list[dict[str, Any]]) -> dict[str, Any] | None:
    units = sorted({row['unit'] for row in series if row['values']})
    summaries = {}
    for unit in units:
        rows = [row for row in series if row['unit'] == unit and row['values']]
        if unit == 'observation':
            summary = summarize_histories([row['values'] for row in rows])
            summary.update(unit=unit, x=list(range(1, summary['length'] + 1)),
                           n_at_point=[summary['n']] * summary['length'])
        else:
            points = [dict(zip(row['x'], row['values'])) for row in rows]
            x = sorted({point for row in points for point in row})
            stats = [summarize_numbers([row[point] for row in points if point in row]) for point in x]
            summary = {key: [stat[key] for stat in stats] for key in ('mean', 'std', 'min', 'max')}
            summary.update(unit=unit, x=x, n=len(rows), length=len(x),
                           n_at_point=[stat['n'] for stat in stats])
        summaries[unit] = summary
    if not summaries:
        return None
    return next(iter(summaries.values())) if len(summaries) == 1 else {'by_unit': summaries}


def attach_histories(
    study_dir: Path | str,
    groups: list[dict[str, Any]],
    experiments: list[dict[str, Any]],
) -> None:
    for group, exp in zip(groups, experiments):
        buckets: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for row in group.get('runs') or []:
            if row.get('status') != STATUS_SUCCEEDED:
                continue
            run_dir = str(row.get('run_dir') or row.get('id') or '')
            if not run_dir:
                continue
            for split, names in load_curve_series(study_dir, run_dir).items():
                split_map = buckets.setdefault(split, {})
                for name, series in names.items():
                    split_map.setdefault(name, []).append(series)
        history: dict[str, dict[str, Any]] = {}
        for split, names in buckets.items():
            packed = {}
            for name, series in names.items():
                summary = summarize_curve_series(series)
                if summary is not None:
                    packed[name] = summary
            if packed:
                history[split] = packed
        exp['history'] = history


def _mean_std(values: list[float]) -> dict[str, float | int | None]:
    return summarize_numbers(values)


def summarize_numbers(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {'mean': None, 'std': None, 'min': None, 'max': None, 'n': 0}
    return {
        'mean': float(statistics.fmean(values)),
        'std': float(statistics.stdev(values)) if len(values) >= 2 else 0.0,
        'min': float(min(values)),
        'max': float(max(values)),
        'n': len(values),
    }


def summarize_histories(series: list[list[float]]) -> dict[str, Any] | None:
    rows = [row for row in series if row]
    if not rows:
        return None
    length = min(len(row) for row in rows)
    means: list[float] = []
    stds: list[float] = []
    mins: list[float] = []
    maxs: list[float] = []
    for i in range(length):
        col = [row[i] for row in rows]
        means.append(float(statistics.fmean(col)))
        stds.append(float(statistics.stdev(col)) if len(col) >= 2 else 0.0)
        mins.append(float(min(col)))
        maxs.append(float(max(col)))
    return {
        'mean': means,
        'std': stds,
        'min': mins,
        'max': maxs,
        'n': len(rows),
        'length': length,
    }


def _numeric_metrics(metrics: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in (metrics or {}).items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out[str(key)] = float(value)
    return out


def _load_result(study_dir: Path, run_dir: str) -> dict[str, Any] | None:
    path = study_dir / RUNS_DIRNAME / run_dir / RESULT_NAME
    try:
        if not path.is_file():
            return None
        return load_result(path)
    except (OSError, TypeError, ValueError, CorruptArtifactError, RecursionError):
        return None


def collect_from_index(study_dir: Path, index: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for exp in index.get('experiments') or []:
        planned = list(exp.get('runs') or [])
        rows: list[dict[str, Any]] = []
        for run in planned:
            run_dir = str(run.get('run_dir') or run.get('id') or '')
            result = _load_result(study_dir, run_dir) if run_dir else None
            row: dict[str, Any] = {
                'id': run.get('id'),
                'run_dir': run_dir,
                'seed': run.get('seed'),
                'tags': list(run.get('tags') or []),
                'status': None,
                'metrics': {},
            }
            if result is not None:
                row['status'] = result.get('status')
                if result.get('status') == STATUS_SUCCEEDED:
                    row['metrics'] = _numeric_metrics(result.get('metrics') or {})
            rows.append(row)
        groups.append(
            {
                'factors': dict(exp.get('factors') or {}),
                'n_planned': len(planned),
                'runs': rows,
            }
        )
    return groups


def summarize(
    groups: list[dict[str, Any]],
    *,
    study: str,
    source_run: str | None,
) -> dict[str, Any]:
    experiments: list[dict[str, Any]] = []
    for group in groups:
        succeeded = [row for row in group['runs'] if row.get('status') == STATUS_SUCCEEDED]
        tags: list[str] = []
        for row in group['runs']:
            tags.extend(row.get('tags') or [])
        keys = sorted({key for row in succeeded for key in row.get('metrics') or {}})
        metrics = {
            key: _mean_std(
                [float(row['metrics'][key]) for row in succeeded if key in row.get('metrics', {})]
            )
            for key in keys
        }
        experiments.append(
            {
                'factors': group['factors'],
                'baseline': 'baseline' in tags,
                'n': len(succeeded),
                'n_planned': int(group.get('n_planned') or 0),
                'metrics': metrics,
                'runs': [
                    {
                        'id': row.get('id'),
                        'seed': row.get('seed'),
                        'status': row.get('status'),
                        'metrics': row.get('metrics') or {},
                    }
                    for row in group['runs']
                ],
            }
        )

    baseline_means: dict[str, float] | None = None
    for exp in experiments:
        if exp['baseline'] and exp['n']:
            baseline_means = {
                key: stat['mean']
                for key, stat in exp['metrics'].items()
                if stat.get('mean') is not None
            }
            break

    for exp in experiments:
        if baseline_means and not exp['baseline'] and exp['n']:
            exp['delta_vs_baseline'] = {
                key: stat['mean'] - baseline_means[key]
                for key, stat in exp['metrics'].items()
                if stat.get('mean') is not None and key in baseline_means
            }
        else:
            exp['delta_vs_baseline'] = None

    complete = bool(experiments) and all(
        exp['n'] == exp['n_planned'] and exp['n_planned'] > 0 for exp in experiments
    )
    return {
        'study': study,
        'source_run': source_run,
        'complete': complete,
        'experiments': experiments,
        'paired': pair_train_eval(experiments),
    }


def _mode_from_factors(factors: dict[str, Any]) -> str:
    raw = factors.get('algorithm.mode')
    if raw is None:
        return 'train'
    return str(raw)


def pair_train_eval(experiments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join train/eval Experiment cells that differ only by ``algorithm.mode``."""
    buckets: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for exp in experiments:
        factors = dict(exp.get('factors') or {})
        shared = {k: v for k, v in factors.items() if k != 'algorithm.mode'}
        token = json.dumps(shared, sort_keys=True, default=str)
        if token not in buckets:
            buckets[token] = {'factors': shared, 'train': None, 'eval': None}
            order.append(token)
        mode = _mode_from_factors(factors)
        if mode == 'eval':
            buckets[token]['eval'] = exp
        else:
            buckets[token]['train'] = exp
    out: list[dict[str, Any]] = []
    for token in order:
        item = buckets[token]
        train = item['train']
        ev = item['eval']
        if train is None and ev is None:
            continue
        row: dict[str, Any] = {'factors': item['factors']}
        if train is not None:
            row['train'] = {
                'metrics': train.get('metrics') or {},
                'n': train.get('n'),
                'runs': train.get('runs') or [],
            }
        if ev is not None:
            row['eval'] = {
                'metrics': ev.get('metrics') or {},
                'n': ev.get('n'),
                'runs': ev.get('runs') or [],
            }
        out.append(row)
    return out


def run_delta(metrics: dict[str, float], baseline_means: dict[str, float] | None) -> dict[str, float] | None:
    if not baseline_means:
        return None
    return {
        key: metrics[key] - baseline_means[key]
        for key in metrics
        if key in baseline_means
    }

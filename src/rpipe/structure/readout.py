"""Read and aggregate existing evidence. No execution or backend imports."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any
import json

from .contracts import AdmissionError, RunSpec, SourceMismatch, project_execution
from .artifacts import contained, identity, read_execution, read_json, safe_error_record, validate_source


_TERMINAL = {'succeeded', 'failed', 'cancelled', 'unknown'}


def read_run(spec: RunSpec) -> dict[str, Any]:
    validate_source(spec)
    path = contained(spec.result_path, spec.run_root)
    result = {}
    if path.exists():
        try:
            result = read_json(path)
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise SourceMismatch(f'invalid result for {spec.id}: {exc}') from exc
        if result.get('identity') != identity(spec):
            raise SourceMismatch(f'result identity differs: {spec.id}')
        if (not isinstance(result.get('status'), str) or result['status'] not in _TERMINAL
                or result.get('quality') is not None and not isinstance(result['quality'], bool)
                or not isinstance(result.get('evidence_complete'), bool)):
            return {**result, 'status': 'unknown', 'quality': None,
                    'evidence_complete': False, 'metadata_status': 'invalid',
                    'busy': (spec.run_dir / '.execution').exists()}
    busy = contained(spec.run_dir / '.execution', spec.run_root).exists()
    execution_path = contained(spec.run_dir / 'execution.json', spec.run_root)
    if execution_path.exists():
        try:
            record, complete = read_execution(spec)
        except SourceMismatch:
            raise
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            return {**result, 'id': spec.id, 'status': 'unknown', 'quality': None,
                    'evidence_complete': False, 'busy': busy, 'metadata_status': 'invalid',
                    'execution_error': safe_error_record(exc)}
        return project_execution(record, result, busy=busy, complete=complete)
    # Existing result-only records stay readable without silently signing old attempts.
    attempts = contained(spec.run_dir / 'attempts', spec.run_root)
    unindexed = attempts.is_dir() and any(attempts.iterdir())
    return {**result, 'id': spec.id,
            'status': 'unknown' if busy or not result and unindexed else result.get('status', 'missing'),
            'quality': result.get('quality'),
            'scientific_status': result.get('status', 'missing'),
            'evidence_complete': bool(result.get('evidence_complete') and not busy), 'busy': busy,
            **({'execution_status': 'legacy_unindexed'} if unindexed else {})}


def read_legacy(run_dir: Path | str) -> dict[str, Any]:
    """Explicit unverified inspection, never migration or provenance re-signing."""
    path = Path(run_dir).resolve()
    result = read_json(path / 'result.json')
    return {'legacy': True, 'evidence_complete': False, 'verified': False, 'result': result}


def aggregate(specs) -> dict[str, Any]:
    runs = []
    missing = []
    groups: dict[str, list[dict]] = {}
    seen = set()
    seeds = set()
    for spec in specs:
        try:
            row = read_run(spec)
        except (OSError, ValueError) as exc:
            row = {'id': spec.id, 'status': 'invalid', 'evidence_complete': False,
                   'error': safe_error_record(exc)}
        group = spec.experiment
        run_key = (spec.source_digest, spec.version, spec.id)
        duplicate = run_key in seen or (group, spec.seed) in seeds
        seen.add(run_key)
        seeds.add((group, spec.seed))
        if duplicate:
            raise AdmissionError('duplicate Run or seed in aggregation request')
        metrics = row.get('metrics', {})
        if not isinstance(metrics, dict):
            row = {**row, 'metadata_status': 'invalid', 'evidence_complete': False, 'metrics': {}}
        elif any(isinstance(v, bool) or isinstance(v, (int, float)) and not _finite(v) for v in metrics.values()):
            row = {**row, 'metadata_status': 'invalid', 'evidence_complete': False}
        runs.append(row)
        groups.setdefault(group, []).append(row)
        if row.get('status') != 'succeeded' or not row.get('evidence_complete'):
            missing.append(spec.id)
    summaries = {}
    for key, rows in groups.items():
        metric_rows = [r.get('metrics', {}) for r in rows if r.get('status') == 'succeeded']
        names = set().union(*(r.keys() for r in metric_rows)) if metric_rows else set()
        metrics = {}
        for name in names:
            values = [r[name] for r in metric_rows if _finite(r.get(name))]
            if values:
                mean = statistics.fmean(values)
                metrics[name] = {'mean': mean, 'std': statistics.stdev(values) if len(values) > 1 else 0.0,
                                 'n': len(values), 'complete': len(values) == len(rows)}
        summaries[key] = {'metrics': metrics, 'complete': all(r.get('status') == 'succeeded'
                                                            and r.get('evidence_complete') for r in rows)}
    return {'runs': runs, 'experiments': summaries, 'missing_runs': missing, 'complete': not missing}


def logs(spec: RunSpec) -> str:
    validate_source(spec)
    folder = contained(spec.assets_dir / 'logs', spec.run_root)
    paths = list(folder.glob('*.log')) + list(folder.glob('events*.jsonl')) if folder.is_dir() else []
    paths += list((spec.run_dir / 'attempts').glob('*/worker.log'))
    return '\n'.join(contained(p, spec.run_root).read_text(encoding='utf-8', errors='replace')
                     for p in sorted(paths) if p.is_file())


def report(specs) -> str:
    body = aggregate(specs)
    lines = ['| Run | Execution | Scientific | Derived | Quality | Evidence complete |',
             '| --- | --- | --- | --- | --- | --- |']
    lines += [f"| {r['id']} | {r['status']} | {r.get('scientific_status', 'missing')} | "
              f"{r.get('derived_status', 'not_run')} | {r.get('quality')} | {r.get('evidence_complete', False)} |"
              for r in body['runs']]
    return '\n'.join(lines) + '\n'


def compare(a: RunSpec, b: RunSpec, *, differences: tuple[str, ...] = ()) -> dict:
    """Paired scientific comparison with each differing condition declared."""
    left_cfg, right_cfg = a.to_config(), b.to_config()
    for cfg in (left_cfg, right_cfg):
        cfg.pop('id', None)
        cfg.pop('description', None)
        cfg.pop('tags', None)
    def flatten(value, prefix=''):
        out = {}
        for key, item in value.items():
            name = f'{prefix}.{key}' if prefix else key
            out.update(flatten(item, name) if isinstance(item, dict) else {name: item})
        return out
    lm_cfg, rm_cfg = flatten(left_cfg), flatten(right_cfg)
    changed = {name for name in set(lm_cfg) | set(rm_cfg) if lm_cfg.get(name) != rm_cfg.get(name)}
    if any(not isinstance(key, str) or key in {'seed', 'id', 'version'} for key in differences):
        raise AdmissionError('paired comparison cannot waive seed or provenance identity')
    if (a.source_digest != b.source_digest or a.version != b.version
            or a.seed != b.seed or not changed.issubset(set(differences))):
        raise AdmissionError('comparison has undeclared conditions or source/version/seed mismatch')
    left, right = read_run(a), read_run(b)
    differences = {}
    lm, rm = left.get('metrics', {}), right.get('metrics', {})
    if not isinstance(lm, dict) or not isinstance(rm, dict):
        raise AdmissionError('invalid optional metrics shape')
    for name in set(lm) | set(rm):
        x, y = lm.get(name), rm.get(name)
        valid = _finite(x) and _finite(y)
        differences[name] = {'left': x, 'right': y,
                             'difference': y-x if valid else None, 'status': 'measured' if valid else 'unknown'}
    return {'left': left, 'right': right, 'metrics': differences, 'changed_conditions': sorted(changed)}

import statistics
from .artifacts import _owned_stream, _read_text, kinds, read_json as load_result
CorruptArtifactError = AdmissionError
PROCESS_NAME = "process.json"
RESULT_NAME = "result.json"
RUNS_DIRNAME = "runs"
STATUS_SUCCEEDED = "succeeded"

def process_path(study_dir: Path | str) -> Path:
    return Path(study_dir) / PROCESS_NAME

def run_process_path(run_root: Path | str) -> Path:
    return Path(run_root) / PROCESS_NAME

def _state_history(study_dir: Path | str, run_dir: str) -> dict[str, dict[str, list[float]]]:
    path = contained(Path(study_dir) / RUNS_DIRNAME / run_dir / 'assets' / kinds.TRACKER_STATE, Path(study_dir))
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
            values = [float(value) for value in history if _finite(value)]
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
    out = _jsonl_curve_series(contained(Path(study_dir) / RUNS_DIRNAME / run_dir / 'assets' / kinds.TRACKER_JSONL, Path(study_dir)))
    for split, names in _state_history(study_dir, run_dir).items():
        for name, values in names.items():
            if name not in out.get(split, {}):
                out.setdefault(split, {})[name] = {'unit': 'observation', 'x': list(range(1, len(values) + 1)), 'values': values}
    return out

def _jsonl_curve_series(path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    records = []
    try:
        with _owned_stream(path.open('rb')) as handle:
            while (line := handle.readline()):
                end = handle.tell()
                try:
                    row = json.loads(line)
                except (ValueError, RecursionError, UnicodeDecodeError):
                    continue
                if isinstance(row, dict):
                    records.append((end - len(line), end, row))
    except OSError:
        pass
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
        split, name = (row.get('split'), row.get('name'))
        if isinstance(split, str) and isinstance(name, str) and _finite(row.get('mean')):
            buckets.setdefault(split, {}).setdefault(name, []).append(row)
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for split, names in buckets.items():
        for name, rows in names.items():
            if all((type(row.get('optimizer_step')) is int and row['optimizer_step'] >= 0 for row in rows)):
                unit, key = ('step', 'optimizer_step')
            elif all(('optimizer_step' not in row and _finite(row.get('epoch')) and (row['epoch'] >= 0) for row in rows)):
                unit, key = ('epoch', 'epoch')
            else:
                unit, key = ('observation', None)
            if key is None:
                x, values = (list(range(1, len(rows) + 1)), [row['mean'] for row in rows])
            else:
                points = {row[key]: row['mean'] for row in rows}
                x = sorted(points)
                values = [points[point] for point in x]
            out.setdefault(split, {})[name] = {'unit': unit, 'x': x, 'values': values}
    return out

def load_tracker_history(study_dir: Path | str, run_dir: str) -> dict[str, dict[str, list[float]]]:
    return {split: {name: series['values'] for name, series in names.items()} for split, names in load_curve_series(study_dir, run_dir).items()}

def summarize_curve_series(series: list[dict[str, Any]]) -> dict[str, Any] | None:
    units = sorted({row['unit'] for row in series if row['values']})
    summaries = {}
    for unit in units:
        rows = [row for row in series if row['unit'] == unit and row['values']]
        if unit == 'observation':
            summary = summarize_histories([row['values'] for row in rows])
            summary.update(unit=unit, x=list(range(1, summary['length'] + 1)), n_at_point=[summary['n']] * summary['length'])
        else:
            points = [dict(zip(row['x'], row['values'])) for row in rows]
            x = sorted({point for row in points for point in row})
            stats = [summarize_numbers([row[point] for row in points if point in row]) for point in x]
            summary = {key: [stat[key] for stat in stats] for key in ('mean', 'std', 'min', 'max')}
            summary.update(unit=unit, x=x, n=len(rows), length=len(x), n_at_point=[stat['n'] for stat in stats])
        summaries[unit] = summary
    if not summaries:
        return None
    return next(iter(summaries.values())) if len(summaries) == 1 else {'by_unit': summaries}

def attach_histories(study_dir: Path | str, groups: list[dict[str, Any]], experiments: list[dict[str, Any]]) -> None:
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
    return {'mean': float(statistics.fmean(values)), 'std': float(statistics.stdev(values)) if len(values) >= 2 else 0.0, 'min': float(min(values)), 'max': float(max(values)), 'n': len(values)}

def summarize_histories(series: list[list[float]]) -> dict[str, Any] | None:
    rows = [row for row in series if row]
    if not rows:
        return None
    length = min((len(row) for row in rows))
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
    return {'mean': means, 'std': stds, 'min': mins, 'max': maxs, 'n': len(rows), 'length': length}

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
            row: dict[str, Any] = {'id': run.get('id'), 'run_dir': run_dir, 'seed': run.get('seed'), 'tags': list(run.get('tags') or []), 'status': None, 'metrics': {}}
            if result is not None:
                row['status'] = result.get('status')
                if result.get('status') == STATUS_SUCCEEDED:
                    row['metrics'] = _numeric_metrics(result.get('metrics') or {})
            rows.append(row)
        groups.append({'factors': dict(exp.get('factors') or {}), 'n_planned': len(planned), 'runs': rows})
    return groups

def summarize(groups: list[dict[str, Any]], *, study: str, source_run: str | None) -> dict[str, Any]:
    experiments: list[dict[str, Any]] = []
    for group in groups:
        succeeded = [row for row in group['runs'] if row.get('status') == STATUS_SUCCEEDED]
        tags: list[str] = []
        for row in group['runs']:
            tags.extend(row.get('tags') or [])
        keys = sorted({key for row in succeeded for key in row.get('metrics') or {}})
        metrics = {key: _mean_std([float(row['metrics'][key]) for row in succeeded if key in row.get('metrics', {})]) for key in keys}
        experiments.append({'factors': group['factors'], 'baseline': 'baseline' in tags, 'n': len(succeeded), 'n_planned': int(group.get('n_planned') or 0), 'metrics': metrics, 'runs': [{'id': row.get('id'), 'seed': row.get('seed'), 'status': row.get('status'), 'metrics': row.get('metrics') or {}} for row in group['runs']]})
    baseline_means: dict[str, float] | None = None
    for exp in experiments:
        if exp['baseline'] and exp['n']:
            baseline_means = {key: stat['mean'] for key, stat in exp['metrics'].items() if stat.get('mean') is not None}
            break
    for exp in experiments:
        if baseline_means and (not exp['baseline']) and exp['n']:
            exp['delta_vs_baseline'] = {key: stat['mean'] - baseline_means[key] for key, stat in exp['metrics'].items() if stat.get('mean') is not None and key in baseline_means}
        else:
            exp['delta_vs_baseline'] = None
    complete = bool(experiments) and all((exp['n'] == exp['n_planned'] and exp['n_planned'] > 0 for exp in experiments))
    return {'study': study, 'source_run': source_run, 'complete': complete, 'experiments': experiments, 'paired': pair_train_eval(experiments)}

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
            row['train'] = {'metrics': train.get('metrics') or {}, 'n': train.get('n'), 'runs': train.get('runs') or []}
        if ev is not None:
            row['eval'] = {'metrics': ev.get('metrics') or {}, 'n': ev.get('n'), 'runs': ev.get('runs') or []}
        out.append(row)
    return out

def run_delta(metrics: dict[str, float], baseline_means: dict[str, float] | None) -> dict[str, float] | None:
    if not baseline_means:
        return None
    return {key: metrics[key] - baseline_means[key] for key in metrics if key in baseline_means}

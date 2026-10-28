"""Compare complete historical trajectories with predeclared PNG estimates.

No training is performed. ``--partial`` draws available observations and their
per-step seed counts; it never declares the four-seed final gates passed.
Completed checkpoint bundles are loaded on CPU, not from diagnostic mirrors.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

# Import NumPy before lazy Torch imports for this Windows runtime.
import numpy as np

STUDY = Path(__file__).resolve().parents[2]
DATA_NAMES = ('MNIST', 'CIFAR10')
MODEL_NAMES = ('linear', 'mlp', 'cnn', 'resnet18')
SEEDS = (0, 1, 2, 3)
STEPS = list(range(200, 80001, 200))
COLORS = {'linear': 'red', 'mlp': 'orange', 'cnn': 'blue', 'resnet18': 'dodgerblue'}
TOLERANCE = 1e-9


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def read_json(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'{path}: expected JSON object')
    return value


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_tracker(root):
    """Keep explicit coordinates, reject duplicate points or hidden restarts."""
    folder = root / 'assets' / 'tracker'
    state_path, scalar_path = folder / 'tracker_state.json', folder / 'scalars.jsonl'
    errors = []
    state = read_json(state_path) if state_path.is_file() else {}
    rows, starts = [], []
    if scalar_path.is_file():
        raw = scalar_path.read_bytes()
        # An active writer can leave its last line unfinished while --partial
        # reads it. Ignore only that uncommitted tail, never a broken full line.
        lines = raw.splitlines(keepends=True)
        for number, line in enumerate(lines, 1):
            try:
                row = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                if number == len(lines) and not line.endswith(b'\n'):
                    continue
                errors.append(f'corrupt scalars line {number}')
                continue
            if not isinstance(row, dict):
                errors.append(f'scalars line {number} is not an object')
            elif row.get('event') == 'start':
                starts.append(row)
            elif row.get('split') == 'test' and row.get('name') == 'Accuracy':
                if type(row.get('optimizer_step')) is not int or not finite(row.get('mean')):
                    errors.append(f'test Accuracy line {number} lacks finite mean or optimizer_step')
                else:
                    rows.append(row)
    if len(starts) > 1 or any(row.get('keep_until') != 0 for row in starts):
        errors.append('tracker contains restart/restore markers; continuous training required')
    x = [row['optimizer_step'] for row in rows]
    y = [float(row['mean']) for row in rows]
    if len(x) != len(set(x)):
        errors.append('duplicate test optimizer_step')
    history = (((state.get('splits') or {}).get('test') or {}).get('Accuracy') or {}).get('history', [])
    if not isinstance(history, list) or not all(finite(value) for value in history):
        errors.append('invalid tracker test/Accuracy history')
        history = []
    return {'x': x, 'values': y, 'rows': rows, 'state': state, 'history': history,
            'start_markers': starts, 'errors': errors}


def checkpoint_summary(root, stem):
    """Read the explicitly selected committed snapshot."""
    from rpipe.structure.artifact.checkpoint import load_snapshot

    path = root / 'assets' / 'checkpoints' / f'{stem}.pt'
    if not path.is_file():
        return None
    body = load_snapshot(path)
    tracker = body.get('tracker') or {}
    return {
        'path': str(path), 'step': body.get('step'),
        'best_metric': body.get('best_metric'), 'best_value': body.get('best_value'),
        'best_accuracy': body.get('best_accuracy'), 'test_accuracy': body.get('test_accuracy'),
        'tracker_step': tracker.get('step'), 'tracker_progress': tracker.get('progress'),
        'test_history': (((tracker.get('splits') or {}).get('test') or {}).get('Accuracy') or {}).get('history', []),
    }


def close(left, right):
    return finite(left) and finite(right) and abs(left - right) <= TOLERANCE


def read_diagnostics(root, run_id, attempt):
    folder = root / 'assets' / 'logs'
    paths, errors = [], []
    for name in ('events.jsonl', 'events.1.jsonl', 'events.2.jsonl', 'events.3.jsonl'):
        path = folder / name
        try:
            if path.is_file():
                paths.append(path)
        except OSError as exc:
            errors.append(f'diagnostic {name} unavailable: {type(exc).__name__}')
    if paths or errors:
        events = []
        for path in paths:
            try:
                lines = path.read_text(encoding='utf-8').splitlines()
            except (OSError, UnicodeError) as exc:
                errors.append(f'diagnostic {path.name} unreadable: {type(exc).__name__}')
                continue
            for number, line in enumerate(lines, 1):
                try:
                    row = json.loads(line)
                except (ValueError, RecursionError):
                    errors.append(f'diagnostic {path.name} line {number} is invalid JSON')
                    continue
                if not isinstance(row, dict):
                    errors.append(f'diagnostic {path.name} line {number} is not an object')
                    continue
                if row.get('task_id') == run_id and (attempt is None or row.get('attempt_id') == attempt):
                    events.append(row)
        return {'format': 'jsonl', 'present': bool(events),
                'resume_count': sum(row.get('event') == 'checkpoint.restored' for row in events), 'errors': errors}
    legacy = folder / 'run.log'
    try:
        text = legacy.read_text(encoding='utf-8') if legacy.is_file() else ''
    except (OSError, UnicodeError) as exc:
        text = ''
        errors.append(f'legacy diagnostics unreadable: {type(exc).__name__}')
    return {'format': 'legacy' if text else 'missing', 'present': bool(text),
            'resume_count': sum('[resume]' in line for line in text.splitlines()), 'errors': errors}


def read_execution_projection(root, result):
    from rpipe.structure.artifact.io import contained
    from rpipe.structure.artifact.io import project_execution

    busy = True
    try:
        owner = root / '.execution'
        busy = owner.exists() or owner.is_symlink()
        index = root / 'execution.json'
        if not index.exists() and not index.is_symlink():
            return {**result, 'status': 'unknown' if busy else 'legacy' if result else 'missing',
                    'scientific_status': result.get('status', 'missing'), 'derived_status': 'not_run',
                    'evidence_complete': False, 'busy': busy,
                    'execution_status': 'legacy_unindexed' if result else 'missing'}
        record = read_json(contained(index, root))
        if not isinstance(record.get('identity'), dict) or record['identity'] != result.get('identity'):
            raise ValueError('execution and scientific result identities differ')
        attempt = record.get('attempt')
        complete = (isinstance(attempt, str) and len(attempt) == 32
                    and all(char in '0123456789abcdef' for char in attempt))
        if complete:
            outcome = read_json(contained(root / 'attempts' / attempt / 'outcome.json', root))
            complete = (json.dumps(record, sort_keys=True, allow_nan=False)
                        == json.dumps(outcome, sort_keys=True, allow_nan=False))
        projection = project_execution(record, result, busy=busy, complete=complete)
        projection['execution_status'] = projection.get('metadata_status', record.get('phase'))
        return projection
    except (OSError, ValueError, TypeError, RecursionError):
        return {**project_execution({}, result, busy=busy, complete=False), 'execution_status': 'invalid'}


def inspect_run(study, entry, data, model, mode, seed, *, expected_source=None):
    root = study / 'runs' / str(entry['run_dir'])
    if not root.resolve().is_relative_to((study / 'runs').resolve()):
        raise ValueError('Run path escapes the explicit Study version')
    result_path = root / 'result.json'
    result = read_json(result_path) if result_path.is_file() else {}
    trajectory = read_tracker(root)
    errors = list(trajectory['errors'])
    diagnostics = read_diagnostics(root, entry['id'], result.get('attempt'))
    errors.extend(diagnostics['errors'])
    execution = read_execution_projection(root, result)
    if result.get('status') == 'succeeded' and not execution['evidence_complete']:
        errors.append('scientific result lacks complete closed execution evidence')
    execute = result.get('execute') if isinstance(result.get('execute'), dict) else {}
    metrics = result.get('metrics') or {}
    row = {'id': entry['id'], 'seed': seed, 'data': data, 'model': model, 'mode': mode,
           'status': (execution['status'] if execution['status'] != 'missing'
                      else 'started' if diagnostics['present'] else 'pending'),
           'scientific_status': result.get('status', 'missing'),
           'derived_status': execution['derived_status'], 'execution_status': execution['execution_status'],
           'evidence_complete': execution['evidence_complete'], 'busy': execution['busy'],
           'identity': result.get('identity') or {}, 'execute': execute,
           'result_path': str(result_path), 'points': len(trajectory['x']),
           'last_optimizer_step': trajectory['x'][-1] if trajectory['x'] else None,
           'optimizer_steps': trajectory['x'], 'accuracy_pct': trajectory['values'],
           'tracker_history_count': len(trajectory['history']),
           'resume_event_count': diagnostics['resume_count'], 'diagnostic_format': diagnostics['format'],
           'metrics': metrics,
           'errors': errors, 'audit': {}}
    if mode == 'train':
        if trajectory['x'] != STEPS[:len(trajectory['x'])]:
            errors.append('train test curve is not an uninterrupted step200..80000 prefix')
        if any(r.get('step') != (index + 1) * 240 for index, r in enumerate(trajectory['rows'])):
            errors.append('train/test internal batch counts differ from 200+40 per evaluation')
    if row['scientific_status'] == 'succeeded':
        if expected_source is not None:
            identity = result.get('identity') or {}
            if (identity.get('id') != entry['id'] or identity.get('source_digest') != expected_source
                    or identity.get('version') != study.name):
                errors.append('result does not match the current source and research version identity')
        if not diagnostics['present']:
            errors.append('succeeded result has no matching Run diagnostics')
        count = execute.get('resume_count')
        if type(count) is not int or count < 0:
            errors.append('succeeded result lacks a valid scientific resume_count')
        if mode == 'train' and (count != 0 or 'resume_path' not in execute or execute['resume_path'] is not None):
            errors.append('continuous training requires an explicit zero restores and no resume_path')
        if len(trajectory['start_markers']) != 1:
            errors.append('succeeded run requires one continuous tracker start marker')
        data_snapshot = ((result.get('structure') or {}).get('data') or {})
        if data_snapshot.get('test_size') != 10000 or data_snapshot.get('test_batch_size') != 250:
            errors.append('succeeded result does not record full test10000/batch250')
        if data_snapshot.get('train_size') != (60000 if data == 'MNIST' else 50000):
            errors.append('succeeded result does not record the full training dataset')
        if mode == 'train':
            if trajectory['x'] != STEPS:
                errors.append('succeeded train requires exactly 400 test points')
            if trajectory['history'] != trajectory['values']:
                errors.append('completed tracker state history differs from scalar values')
            latest, best = checkpoint_summary(root, 'latest'), checkpoint_summary(root, 'best')
            row['latest_checkpoint'], row['best_checkpoint'] = latest, best
            if latest is None or latest['step'] != 80000:
                errors.append('latest committed checkpoint is missing or not step80000')
            elif latest['test_history'] != trajectory['values'] or (latest['tracker_progress'] or {}).get('step') != 80000:
                errors.append('latest committed checkpoint trajectory/progress differs from final scalars')
            maximum = max(trajectory['values']) if trajectory['values'] else None
            best_steps = [x for x, y in zip(trajectory['x'], trajectory['values']) if close(y, maximum)]
            checks = {
                'best_equals_global_max': best is not None and close(best.get('best_accuracy'), maximum),
                'best_value_equals_global_max': best is not None and close(best.get('best_value'), maximum),
                'best_metric_accuracy': best is not None and best.get('best_metric') == 'Accuracy',
                'best_step_at_global_max': best is not None and best.get('step') in best_steps,
                'best_test_accuracy_equals_max': best is not None and close(best.get('test_accuracy'), maximum),
                'result_best_equals_global_max': close(metrics.get('best_accuracy'), maximum),
                'result_last_accuracy_matches_curve': bool(trajectory['values']) and close(metrics.get('accuracy'), trajectory['values'][-1]),
            }
            row['audit'] = {'curve_global_max_accuracy_pct': maximum, 'global_max_steps': best_steps, **checks}
            errors.extend(name for name, passed in checks.items() if not passed)
        else:
            if len(trajectory['values']) != 1 or trajectory['history'] != trajectory['values']:
                errors.append('independent eval requires one consistent tracker observation')
            if not trajectory['rows'] or trajectory['rows'][0].get('step') != 40:
                errors.append('independent eval requires exactly 40 test batches')
            if not close(metrics.get('eval_accuracy'), trajectory['values'][0] if trajectory['values'] else None):
                errors.append('independent eval result accuracy differs from tracker')
    row['evidence_complete'] = row['evidence_complete'] and not errors
    return row


def threshold_gate(observed, expected, limit):
    error = abs(observed - expected)
    return {'observed': float(observed), 'reference_image_estimate': float(expected),
            'absolute_error_pp': float(error), 'threshold_pp': limit, 'passed': bool(error <= limit)}


def compare(study, reference, *, partial=False):
    from rpipe.structure.artifact.provenance import source_digest
    expected_source = source_digest(STUDY)
    contract = reference['acceptance_contract']
    report = {'schema': 'rpipe.historical_curve_comparison.v1',
              'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
              'study': str(study), 'source_sha': reference['source_sha'],
              'reference_evidence_type': reference['evidence_type'],
              'reference_file_sha256': sha256(STUDY / 'docs' / 'REFERENCE_CURVES.json'),
              'comparison_script_sha256': sha256(Path(__file__)),
              'partial_mode': partial, 'acceptance_contract': contract,
              'complete': False, 'final_gates_applied': False, 'passed': None,
              'status': 'incomplete', 'errors': [], 'missing_runs': [], 'runs': [], 'experiments': []}
    index_path = study / 'index.json'
    index = read_json(index_path) if index_path.is_file() else {'experiments': []}
    if not index_path.is_file():
        report['missing_index'] = True
    table = {}
    for experiment in index.get('experiments', []):
        factors = experiment['factors']
        data, model, mode = (factors.get(key) for key in ('data.name', 'model.name', 'algorithm.mode'))
        if data not in DATA_NAMES or model not in MODEL_NAMES or mode not in ('train', 'eval'):
            report['errors'].append(f'unexpected experiment factors: {factors}')
            continue
        for entry in experiment['runs']:
            seed = entry.get('seed')
            key = (data, model, mode, seed)
            if type(seed) is not int or seed not in SEEDS or key in table:
                report['errors'].append(f'duplicate/unsupported run factors: {key}')
                continue
            try:
                row = inspect_run(study, entry, data, model, mode, seed, expected_source=expected_source)
            except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as exc:
                row = {'id': entry.get('id'), 'data': data, 'model': model, 'mode': mode, 'seed': seed,
                       'status': 'unreadable', 'points': 0, 'optimizer_steps': [], 'accuracy_pct': [],
                       'errors': [f'{type(exc).__name__}: {exc}']}
            table[key] = row
            report['runs'].append(row)
            report['errors'].extend(f'{row["id"]}: {error}' for error in row['errors'])
    expected = [(data, model, mode, seed) for data in DATA_NAMES for model in MODEL_NAMES
                for mode in ('train', 'eval') for seed in SEEDS]
    report['missing_runs'] = [dict(zip(('data', 'model', 'mode', 'seed'), key)) for key in expected if key not in table]
    report['run_counts'] = {mode: dict(Counter(row['status'] for row in report['runs'] if row['mode'] == mode))
                            for mode in ('train', 'eval')}
    complete = (not report['missing_runs'] and len(table) == 64
                and all(row['status'] == 'succeeded' and row.get('evidence_complete') for row in table.values()))
    for data in DATA_NAMES:
        for model in MODEL_NAMES:
            seeds = {seed: table[(data, model, 'train', seed)] for seed in SEEDS if (data, model, 'train', seed) in table}
            points = {seed: dict(zip(row['optimizer_steps'], row['accuracy_pct'])) for seed, row in seeds.items() if not row['errors']}
            x = sorted(set().union(*(set(value) for value in points.values()))) if points else []
            values = [[curve[step] for curve in points.values() if step in curve] for step in x]
            body = {'data': data, 'model': model, 'seeds_with_observations': sorted(seed for seed, curve in points.items() if curve),
                    'completed_train_seeds': sorted(seed for seed, row in seeds.items()
                                                    if row['status'] == 'succeeded' and row.get('evidence_complete') and not row['errors']),
                    'optimizer_steps': x, 'seed_count_by_step': [len(value) for value in values],
                    'mean_accuracy_pct': [float(np.mean(value)) for value in values],
                    'population_std_accuracy_pp': [float(np.std(value, ddof=0)) for value in values],
                    'gates_applied': False, 'gates': {}, 'passed': None}
            for seed in SEEDS:
                train, evaluation = table.get((data, model, 'train', seed)), table.get((data, model, 'eval', seed))
                if train and evaluation and train.get('scientific_status') == evaluation.get('scientific_status') == 'succeeded':
                    best = train.get('best_checkpoint') or {}
                    actual = evaluation['accuracy_pct'][0] if evaluation['accuracy_pct'] else None
                    eval_step = evaluation['optimizer_steps'][0] if evaluation['optimizer_steps'] else None
                    execute = evaluation.get('execute') or {}
                    resume_path, resume_count = execute.get('resume_path'), execute.get('resume_count')
                    resume_step = execute.get('step')
                    train_identity, eval_identity = train.get('identity') or {}, evaluation.get('identity') or {}
                    identities_match = (train_identity.get('id') == train['id']
                        and eval_identity.get('id') == evaluation['id']
                        and train_identity.get('source_digest') == eval_identity.get('source_digest') == expected_source
                        and train_identity.get('version') == eval_identity.get('version') == study.name
                        and train['seed'] == evaluation['seed'])
                    single_restore = (type(resume_count) is int and resume_count == 1
                        and type(resume_step) is int and resume_step == best.get('step'))
                    source_match = (identities_match and isinstance(resume_path, str) and bool(resume_path)
                        and Path(resume_path).resolve() == Path(best.get('path', '')).resolve())
                    audit = {'train_id': train['id'], 'best_step': best.get('step'), 'eval_step': eval_step,
                             'best_accuracy_pct': best.get('best_accuracy'), 'eval_accuracy_pct': actual,
                             'correct_samples': round(actual * 100) if finite(actual) else None,
                             'eval_equals_best': close(actual, best.get('best_accuracy')),
                             'eval_step_equals_best': type(eval_step) is int and eval_step == best.get('step') == resume_step,
                             'resume_evidence_source': 'result.execute', 'scientific_resume_count': resume_count,
                             'source_identity_matches': identities_match,
                             'reported_actual_resume_path': resume_path,
                             'single_resume_event_at_best_step': single_restore,
                             'eval_loads_matching_train_best': source_match}
                    audit['passed'] = bool(audit['eval_equals_best'] and audit['eval_step_equals_best'] and source_match and audit['single_resume_event_at_best_step'])
                    evaluation['best_source_audit'] = audit
                    if not audit['passed']:
                        report['errors'].append(f'{evaluation["id"]}: independent eval does not match sibling train best')
            if complete and not report['errors'] and not partial:
                target = reference['datasets'][data]['models'][model]
                mean = np.array(body['mean_accuracy_pct'])
                if x != STEPS or body['seed_count_by_step'] != [4] * 400:
                    report['errors'].append(f'{data}/{model}: complete mean lacks four matching trajectories')
                else:
                    gates = {
                        'endpoint': threshold_gate(mean[-1], target['endpoint_accuracy_pct_estimate'], contract['endpoint_abs_difference_pp'][data]),
                        'late_50_mean': threshold_gate(np.mean(mean[350:400]), target['late_50_mean_accuracy_pct_estimate'], contract['late_50_mean_abs_difference_pp'][data]),
                    }
                    refs = {point['slot']: point['accuracy_pct_estimate'] for point in target['sparse_points']}
                    anchor_errors = [abs(float(mean[slot]) - refs[slot]) for slot in contract['curve_comparison_slots']]
                    gates['anchor_mae'] = {'observed_error_pp': float(np.mean(anchor_errors)), 'threshold_pp': contract['anchor_mean_absolute_error_pp'][data],
                                           'passed': bool(np.mean(anchor_errors) <= contract['anchor_mean_absolute_error_pp'][data])}
                    gates['anchor_max'] = {'observed_error_pp': float(np.max(anchor_errors)), 'threshold_pp': contract['anchor_max_absolute_error_pp'][data],
                                           'passed': bool(np.max(anchor_errors) <= contract['anchor_max_absolute_error_pp'][data])}
                    gates['late_50_temporal_std'] = {'observed_pp': float(np.std(mean[350:400], ddof=0)),
                                                    'threshold_pp': contract['late_50_temporal_population_std_max_pp'][data],
                                                    'passed': bool(np.std(mean[350:400], ddof=0) <= contract['late_50_temporal_population_std_max_pp'][data])}
                    body['anchors'] = [{'slot': slot, 'optimizer_step': STEPS[slot], 'observed_accuracy_pct': float(mean[slot]),
                                        'reference_image_estimate_pct': refs[slot], 'absolute_error_pp': error}
                                       for slot, error in zip(contract['curve_comparison_slots'], anchor_errors)]
                    body['gates'], body['gates_applied'] = gates, True
                    body['passed'] = all(gate['passed'] for gate in gates.values())
            report['experiments'].append(body)
    report['complete'] = complete and not report['errors']
    report['final_gates_applied'] = report['complete'] and not partial
    if not report['final_gates_applied']:
        # A later model's independent-eval audit can invalidate the global
        # completeness prerequisite after an earlier model was inspected.
        for body in report['experiments']:
            body['gates_applied'], body['gates'], body['passed'] = False, {}, None
            body.pop('anchors', None)
    if report['final_gates_applied']:
        report['model_order_gates'] = {}
        for data in DATA_NAMES:
            group = {row['model']: float(np.mean(row['mean_accuracy_pct'][350:400])) for row in report['experiments'] if row['data'] == data}
            order = contract['late_50_mean_order_high_to_low']
            passed = all(group[left] > group[right] for left, right in zip(order, order[1:]))
            report['model_order_gates'][data] = {'late_50_mean_accuracy_pct': group, 'expected_descending_order': order, 'passed': passed}
        report['passed'] = all(row['passed'] for row in report['experiments']) and all(row['passed'] for row in report['model_order_gates'].values())
        report['status'] = 'passed' if report['passed'] else 'failed'
    elif report['errors']:
        report['status'] = 'invalid'
    elif partial:
        report['status'] = 'partial'
    return report


def plot_report(report, reference, destination):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import uuid

    from rpipe.structure.artifact.io import atomic_replace

    tmp = destination.with_name(f'{uuid.uuid4().hex}.tmp')
    owned = False
    fig = plt.figure(figsize=(14, 5), dpi=160)
    try:
        axes = fig.subplots(1, 2)
        for ax, data in zip(axes, DATA_NAMES):
            for model in MODEL_NAMES:
                source = reference['datasets'][data]['models'][model]['sparse_points']
                ax.scatter([point['step'] for point in source], [point['accuracy_pct_estimate'] for point in source],
                           s=24, marker='x', color=COLORS[model], alpha=0.65)
                body = next(row for row in report['experiments'] if row['data'] == data and row['model'] == model)
                if body['optimizer_steps']:
                    x, mean, std = map(np.array, (body['optimizer_steps'], body['mean_accuracy_pct'], body['population_std_accuracy_pp']))
                    counts = body['seed_count_by_step']
                    ax.plot(x, mean, color=COLORS[model], label=f'{model}: n={min(counts)}..{max(counts)}')
                    ax.fill_between(x, mean - std, mean + std, color=COLORS[model], alpha=0.12)
                else:
                    ax.plot([], [], color=COLORS[model], label=f'{model}: no current points')
            ax.set_title(data)
            ax.set_xlabel('Optimizer step (reference slot j maps to (j+1)*200)')
            ax.set_ylabel('Full test Accuracy (%)')
            ax.set_xlim(0, 80000)
            ax.grid(alpha=0.3)
            ax.legend(fontsize=8)
        label = 'FINAL' if report['final_gates_applied'] else 'INCOMPLETE / PROVISIONAL'
        fig.suptitle(f'{label}: current seed mean +/- population std; crosses = PNG estimates\n'
                     'Reference image values are not recovered original training metrics', fontsize=11)
        fig.tight_layout()
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            mode = destination.stat().st_mode
        except FileNotFoundError:
            mode = None
        stream = tmp.open('xb')
        owned = True
        try:
            fig.savefig(stream, format='png')
        except BaseException:
            try:
                stream.close()
            except OSError:
                pass
            raise
        else:
            stream.close()
        if mode is not None:
            tmp.chmod(mode)
        atomic_replace(tmp, destination)
    finally:
        try:
            if owned:
                try:
                    tmp.unlink(missing_ok=True)
                except PermissionError:
                    try:
                        tmp.chmod(0o600)
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass
                except OSError:
                    pass
        finally:
            plt.close(fig)


def main(argv=None):
    from rpipe.structure.artifact.io import atomic_write_text

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study-root', type=Path, required=True, help='Explicit research version output root')
    parser.add_argument('--reference', type=Path, default=STUDY / 'docs' / 'REFERENCE_CURVES.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--partial', action='store_true')
    args = parser.parse_args(argv)
    study = args.study_root.resolve()
    output = args.output.resolve()
    checkout = STUDY.parents[1]
    if output.is_relative_to(checkout) or checkout.is_relative_to(output):
        parser.error('output must be separate from the source checkout')
    reference = read_json(args.reference)
    report = compare(study, reference, partial=args.partial)
    report['reference_file_sha256'] = sha256(args.reference)
    output.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output / 'COMPARISON.json', json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    plot_report(report, reference, output / 'figures' / 'historical_comparison.png')
    print(json.dumps({'status': report['status'], 'complete': report['complete'],
                      'final_gates_applied': report['final_gates_applied'], 'passed': report['passed'],
                      'run_counts': report['run_counts'], 'errors': report['errors']}, ensure_ascii=False))
    if report['errors'] or (not args.partial and not report['complete']):
        return 2
    if report['final_gates_applied'] and not report['passed']:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

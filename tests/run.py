"""Unified pytest launcher, batch supervision, and disk-only evidence reports."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


def _expr(parts: list[str]) -> str:
    return ' and '.join(f'({part})' for part in parts if part)


def save_json(path: Path, value: dict) -> None:
    staging = path.with_suffix('.tmp')
    staging.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n',
                       encoding='utf-8', errors='backslashreplace')
    for attempt in range(6):
        try:
            staging.replace(path)
            break
        except OSError as error:
            if getattr(error, 'winerror', None) not in {5, 32, 33} or attempt == 5:
                raise
            time.sleep(.01 * 2 ** attempt)


def append_event(path: Path, value: dict) -> None:
    encoded = (json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8', errors='backslashreplace')
    with path.open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell():
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b'\n':
                handle.write(b'\n')
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def read_events(out_dir: Path) -> tuple[list[dict], list[str]]:
    events, evidence_errors, seen = [], [], set()
    event_file = out_dir / 'events.jsonl'
    for number, raw in enumerate(event_file.read_bytes().splitlines() if event_file.exists() else [], 1):
        if not raw.strip():
            continue
        try:
            line = raw.decode('utf-8')
            event = json.loads(line)
            key = (event['test_id'], event.get('attempt', 1))
            if not isinstance(key[0], str) or type(key[1]) is not int or key[1] < 1:
                raise ValueError('invalid test_id/attempt')
            if event.get('status') not in {'passed', 'failed', 'measured', 'completed', 'error', 'skipped', 'xfail', 'xpass', 'timeout'}:
                raise ValueError('invalid execution status')
            duration = event.get('duration_ms')
            if duration is not None and (type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0):
                raise ValueError('invalid duration_ms')
            if key in seen:
                raise ValueError('duplicate test_id/attempt')
            seen.add(key)
            events.append(event)
        except (ValueError, KeyError, TypeError) as error:
            evidence_errors.append(f'events.jsonl:{number}: {type(error).__name__}: {error}')
    return events, evidence_errors


def record_unexecuted(out_dir: Path, reason: str) -> None:
    manifest = json.loads((out_dir / 'manifest.json').read_text(encoding='utf-8'))
    events, _ = read_events(out_dir)
    recorded = {event['test_id'] for event in events}
    unexecuted = {entry['test_id']: entry for entry in manifest.get('not_executed', [])}
    for test_id in set(manifest.get('selected', [])) - recorded:
        unexecuted[test_id] = {'test_id': test_id, 'reason': reason}
    manifest['not_executed'] = [unexecuted[test_id] for test_id in sorted(unexecuted)]
    save_json(out_dir / 'manifest.json', manifest)


def write_report(out_dir: Path, exitstatus: int | None = None) -> dict:
    """Rebuild from saved facts, including a recoverable interrupted JSONL tail."""
    manifest = json.loads((out_dir / 'manifest.json').read_text(encoding='utf-8'))
    exitstatus = manifest.get('exit_status', 2) if exitstatus is None else int(exitstatus)
    events, evidence_errors = read_events(out_dir)
    latest, histories = {}, {}
    for event in sorted(events, key=lambda entry: entry.get('attempt', 1)):
        latest[event['test_id']] = event
        histories.setdefault(event['test_id'], []).append(event)
    counts: dict[str, int] = {}
    for event in latest.values():
        status = event.get('status') or 'unknown'
        counts[status] = counts.get(status, 0) + 1
    selected = set(manifest.get('selected', []))
    required = set(manifest.get('required', manifest.get('selected', [])))
    missing = sorted(selected - latest.keys())
    missing_required = sorted(required - latest.keys())
    complete_statuses = {'passed', 'measured', 'completed'}
    required_incomplete = sorted(test_id for test_id in required
                                 if latest.get(test_id, {}).get('status') not in complete_statuses)
    flaky = sorted(test_id for test_id, history in histories.items()
                   if len(history) > 1 and history[-1].get('status') in complete_statuses
                   and any(e.get('status') not in complete_statuses for e in history[:-1]))
    unknown_costs = sum(event.get('duration_ms') is None for event in events)
    actual = None if unknown_costs else sum(event['duration_ms'] for event in events)
    blocking = bool(exitstatus or missing or required_incomplete or evidence_errors
                    or manifest.get('collection_complete') is False
                    or any(e.get('status') in {'failed', 'error', 'timeout'} for e in latest.values()))
    summary = {
        'case_count': len(latest), 'attempt_count': len(events), 'counts': counts,
        'missing': missing, 'missing_required': missing_required,
        'required_incomplete': required_incomplete, 'flaky': flaky,
        'actual_wall_time_ms': actual, 'evidence_errors': evidence_errors, 'blocking': blocking,
        'unknown_cost_attempts': unknown_costs,
        'quality_pass_count': sum(event.get('verdict') == 'pass' for event in latest.values()),
        'quality_fail_count': sum(event.get('verdict') == 'fail' for event in latest.values()),
        'measurement_completed_count': counts.get('measured', 0),
        'plan_complete': not required_incomplete and manifest.get('collection_complete', True),
        'evidence_complete': not (missing or missing_required or evidence_errors) and manifest.get('collection_complete', True),
    }
    lines = ['# Test report', '', f'- run_id: `{manifest["run_id"]}`',
             f'- plan_id: `{manifest.get("plan_id", "ad_hoc")}`', f'- exit_status: {exitstatus}',
             f'- cases: {len(latest)}; attempts: {len(events)}; attempt wall time: {actual} ms',
             f'- quality pass/fail: {summary["quality_pass_count"]}/{summary["quality_fail_count"]}; measured: {counts.get("measured", 0)}',
             f'- blocking: {str(blocking).lower()}', '', '| status | count |', '|---|---:|']
    lines.extend(f'| {status} | {counts[status]} |' for status in sorted(counts))
    lines.extend(['', '批次尝试：'])
    lines.extend(f'- attempt {attempt["attempt"]}: {attempt["status"]}, exit_status={attempt.get("exit_status")}'
                 for attempt in manifest.get('attempts', []))
    lines.extend(['', f'未取得完整结果：{len(missing)}'])
    lines.extend(f'- `{test_id}`' for test_id in missing)
    for title, entries in [('必需合同未完成', required_incomplete), ('flaky（重试后完成）', flaky),
                           ('证据错误', evidence_errors)]:
        lines.extend(['', f'{title}：{len(entries)}'])
        lines.extend(f'- `{entry}`' for entry in entries)
    lines.extend(['', '未验证及豁免：'])
    for name in ('not_selected', 'not_executed', 'exclusions'):
        lines.extend(f'- `{entry["test_id"]}`: {entry["reason"]}' for entry in manifest.get(name, []))
    lines.extend(['', '数值测量完成记 `measured`，质量判定为 null。skipped/xfail/xpass 单列，不算普通通过。',
                  '重试保留全部尝试，耗时合计全部尝试；整批墙钟与共享准备另见 manifest。', ''])
    (out_dir / 'report.md').write_text('\n'.join(lines), encoding='utf-8', errors='backslashreplace')
    save_json(out_dir / 'summary.json', summary)
    return summary


def _stop_owned(child: subprocess.Popen) -> dict:
    # Only this Popen child is supervised; descendant cleanup belongs to its tests/runner.
    cleanup = {'pytest_process': 'unknown', 'descendants': 'unconfirmed', 'all_resources_released': None}
    try:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        cleanup['pytest_process'] = 'stopped'
    except (OSError, subprocess.SubprocessError) as error:
        cleanup['error'] = f'{type(error).__name__}: {error}'[:4000]
    return cleanup


def _interrupted_event(out_dir: Path, attempt: int, status: str, reason: str) -> None:
    active_path = out_dir / 'active.json'
    if not active_path.exists():
        return
    active = json.loads(active_path.read_text(encoding='utf-8'))
    if not active or active.get('attempt') != attempt:
        return
    events, _ = read_events(out_dir)
    if any(event['test_id'] == active['test_id'] and event.get('attempt', 1) == attempt for event in events):
        return
    elapsed = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(active['started_at'])).total_seconds() * 1000)
    active.update(status=status, duration_ms=elapsed, verdict=None, category=None,
                  failure={'when': 'supervisor', 'longrepr': reason})
    active['cost']['actual_wall_time_ms'] = elapsed
    append_event(out_dir / 'events.jsonl', active)
    save_json(active_path, {})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run RPipe tests; timeout/retries supervise whole pytest batches.')
    parser.add_argument('--level', choices=('unit', 'integration', 'e2e'))
    parser.add_argument('--type', dest='test_type', choices=('location', 'content', 'physical'))
    parser.add_argument('--priority', choices=('p1', 'p2', 'p3'))
    parser.add_argument('--result-type', choices=('categorical', 'numeric'))
    parser.add_argument('--cost-class', action='append', choices=('c1', 'c2', 'c3', 'c4'), default=[])
    parser.add_argument('--layer', help='architecture layer marker, e.g. structure_layer')
    parser.add_argument('--module', help='module_* marker without prefix, e.g. control')
    parser.add_argument('--fast', action='store_true', help='unit + p1 + c1, exclude slow/external/gpu')
    parser.add_argument('--core', action='store_true', help='unit c1 and c2, exclude slow/external/gpu')
    parser.add_argument('--all', action='store_true', help='select every collected test')
    parser.add_argument('--results-root', help='external directory for test evidence')
    parser.add_argument('--plan-id')
    parser.add_argument('--environment-id', default='cpu_ci' if os.environ.get('CI') else 'local')
    parser.add_argument('--required', action='append', help='exact required nodeid; default: selected minus static skip exemptions')
    parser.add_argument('--timeout-seconds', type=float, default=600, help='hard timeout of each owned pytest process (default: 600)')
    parser.add_argument('--budget-seconds', type=float, help='total launcher wall budget, including all retries')
    parser.add_argument('--retries', type=int, default=0, help='retry normally exited failed batches; never timeout/interrupted batches')
    parser.add_argument('--report', type=Path, help='rebuild a saved run report without collecting or executing tests')
    parser.add_argument('pytest_args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.report:
        return int(write_report(args.report.resolve())['blocking'])
    if (not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0 or args.retries < 0
            or args.budget_seconds is not None and (not math.isfinite(args.budget_seconds) or args.budget_seconds <= 0)):
        parser.error('timeouts/budgets must be positive finite seconds; retries must be nonnegative')
    extra = list(args.pytest_args)
    if extra and extra[0] == '--':
        extra = extra[1:]
    parts, cost_classes = [], list(args.cost_class)
    if args.fast:
        parts.append('unit and p1 and not slow and not external and not gpu')
        cost_classes = cost_classes or ['c1']
    elif args.core:
        parts.append('unit and not slow and not external and not gpu')
        cost_classes = cost_classes or ['c1', 'c2']
    parts.extend(value for value in (args.level, args.test_type, args.priority, args.layer) if value)
    if args.module:
        parts.append(args.module if args.module.startswith('module_') else f'module_{args.module}')
    cmd = [sys.executable, '-m', 'pytest']
    expr = _expr(parts)
    if expr:
        cmd.extend(['-m', expr])
    for cost_class in cost_classes:
        cmd.extend(['--cost-class', cost_class])
    cmd.extend(extra)
    root = Path(args.results_root or os.environ.get('RPIPE_TEST_RESULTS_ROOT') or Path(tempfile.gettempdir()) / 'rpipe-tests').resolve()
    repo = Path(__file__).resolve().parent.parent
    if root == repo or repo in root.parents:
        parser.error('results-root must be outside the source checkout')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ_') + uuid.uuid4().hex[:8]
    out_dir = root / run_id
    out_dir.mkdir(parents=True)
    budget = args.budget_seconds or args.timeout_seconds * (args.retries + 1)
    plan = {
        'plan_id': args.plan_id or ('fast' if args.fast else 'core' if args.core else 'all' if args.all else 'ad_hoc'),
        'required': args.required, 'selection': {'markexpr': expr, 'cost_class': cost_classes,
                                              'result_type': args.result_type, 'pytest_args': extra},
        'environment_id': args.environment_id, 'timeout_seconds': args.timeout_seconds,
        'budget_seconds': budget, 'retries': args.retries, 'timeout_scope': 'pytest_process', 'retry_scope': 'batch',
        'retry_condition': 'normal failed pytest exit only; no replay after timeout, interruption, launch or cleanup uncertainty',
        'capabilities': {'hard_memory_limit': False, 'hard_per_test_timeout': False, 'descendant_supervision': False},
        'resources': {'parallel_batches': 1, 'gpu_count': 0},
        'blocking': {'missing_selected': True, 'required_statuses': ['passed', 'measured', 'completed'],
                     'failure_statuses': ['failed', 'error', 'timeout'], 'flaky': False},
        'budget_source': 'CLI or provisional 600-second CPU batch timeout; not a performance threshold',
    }
    save_json(out_dir / 'manifest.json', {
        'run_id': run_id, 'started_at': datetime.now(timezone.utc).isoformat(), 'plan_id': plan['plan_id'],
        'plan': plan, 'command': cmd, 'environment_id': args.environment_id,
        'selected': [], 'required': args.required or [], 'attempts': [], 'collection_complete': False,
    })
    (out_dir / 'events.jsonl').touch()
    env = dict(os.environ, RPIPE_TEST_RESULTS_ROOT=str(root), RPIPE_TEST_RUN_DIR=str(out_dir),
               RPIPE_TEST_PLAN=json.dumps(plan), RPIPE_TEST_OWNER_PID=str(os.getpid()))
    print(subprocess.list2cmdline(cmd), flush=True)
    started, code = time.monotonic(), 1
    for attempt in range(1, args.retries + 2):
        remaining = budget - (time.monotonic() - started)
        if remaining <= 0:
            break
        env['RPIPE_TEST_ATTEMPT'] = str(attempt)
        manifest = json.loads((out_dir / 'manifest.json').read_text(encoding='utf-8'))
        manifest['attempts'].append({'attempt': attempt, 'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'running'})
        save_json(out_dir / 'manifest.json', manifest)
        child, status = None, 'completed'
        cleanup = {'pytest_process': 'not_started', 'descendants': 'unconfirmed', 'all_resources_released': None}
        try:
            child = subprocess.Popen(cmd, env=env, stdout=sys.stdout, stderr=sys.stderr)
            cleanup['pytest_process'] = 'running'
            code = child.wait(timeout=min(args.timeout_seconds, remaining))
            cleanup['pytest_process'] = 'exited'
            status = 'completed' if code == 0 else 'interrupted' if code == 2 else 'failed'
        except subprocess.TimeoutExpired:
            cleanup = _stop_owned(child)
            code, status = 1, 'timeout'
            _interrupted_event(out_dir, attempt, status, 'owned pytest process exceeded its batch timeout/wall budget')
        except KeyboardInterrupt:
            if child is not None:
                cleanup = _stop_owned(child)
            code, status = 2, 'interrupted'
            _interrupted_event(out_dir, attempt, 'error', 'launcher interrupted; only its owned pytest process was stopped')
        except OSError as error:
            code, status = 3, 'launch_error'
            print(f'{type(error).__name__}: {error}', file=sys.stderr)
        manifest = json.loads((out_dir / 'manifest.json').read_text(encoding='utf-8'))
        elapsed = time.monotonic() - started
        manifest['attempts'][-1].update(status=status, exit_status=code,
                                       actual_wall_time_ms=(elapsed - (budget - remaining)) * 1000, cleanup=cleanup)
        manifest.update(exit_status=code, actual_wall_time_ms=elapsed * 1000)
        save_json(out_dir / 'manifest.json', manifest)
        record_unexecuted(out_dir, f'batch attempt {attempt} ended with {status}, exit_status={code}')
        summary = write_report(out_dir, code)
        if code == 0 and summary['blocking']:
            code = 1
        if code != 1 or status != 'failed' or cleanup['pytest_process'] != 'exited' or attempt > args.retries:
            break
    manifest = json.loads((out_dir / 'manifest.json').read_text(encoding='utf-8'))
    manifest.update(exit_status=code, actual_wall_time_ms=(time.monotonic() - started) * 1000,
                    finished_at=datetime.now(timezone.utc).isoformat())
    save_json(out_dir / 'manifest.json', manifest)
    write_report(out_dir, code)
    return code


if __name__ == '__main__':
    raise SystemExit(main())

"""Unified non-interactive test entry (TESTING.md 附录：RPipe)."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from _process_tree import ProcessTree


def _expr(parts: list[str]) -> str:
    return ' and '.join(f'({part})' for part in parts if part)


def _diagnostic(message: str, execution: dict) -> None:
    try:
        print(message, file=sys.stderr, flush=True)
    except BaseException as error:  # noqa: BLE001 -- diagnostics and cancellation must preserve the first error.
        execution['diagnostic_errors'].append(repr(error))


def _save_execution(out_dir: Path, execution: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / 'execution.json'
    temporary = path.with_name(f'.execution.json-{uuid.uuid4().hex}.tmp')
    temporary.write_text(json.dumps(execution, indent=2, ensure_ascii=False) + '\n',
                         encoding='utf-8', errors='backslashreplace')
    os.replace(temporary, path)


def _run(cmd: list[str], timeout: float) -> int:
    run_id = uuid.uuid4().hex
    out_dir = Path(__file__).resolve().parent.parent / '.tmp/test-results' / run_id
    execution = {'command': cmd, 'timeout_seconds': timeout, 'status': 'starting',
                 'exit_code': None, 'process_id': None, 'process_returncode': None,
                 'cleanup_confirmed': None, 'cleanup_errors': [], 'diagnostic_errors': [], 'persistence_errors': [],
                 'owned_processes_before_cleanup': None, 'manifest_available': False}
    start = time.monotonic()
    deadline = start + timeout
    tree = ProcessTree()
    proc = None
    primary = None
    try:
        proc = tree.start(cmd, env=dict(os.environ, RPIPE_TEST_RUN_ID=run_id),
                          stdout=sys.stdout, stderr=sys.stderr)
        execution['process_id'] = proc.pid
        execution['exit_code'] = tree.wait(max(0, deadline - time.monotonic()))
        execution['status'] = 'completed'
    except BaseException as error:  # noqa: BLE001 -- diagnostics and cancellation must preserve the first error.
        primary = error
        timed_out = isinstance(error, subprocess.TimeoutExpired)
        execution.update(status='timeout' if timed_out else 'interrupted' if isinstance(error, KeyboardInterrupt) else 'error',
                         error=repr(error), exit_code=124 if timed_out else 130 if isinstance(error, KeyboardInterrupt) else None)
    finally:
        proc = tree.proc
        failures = execution['cleanup_errors']
        if proc is not None:
            execution['process_id'] = proc.pid
            try:
                execution['owned_processes_before_cleanup'] = tree.terminate(timeout=10)
            except BaseException as cleanup:  # noqa: BLE001 -- cleanup must not replace cancellation or the original failure.
                failures.append({'phase': 'process-tree', 'error': repr(cleanup)})
            try:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=10)
            except BaseException as cleanup:  # noqa: BLE001 -- cleanup must not replace cancellation or the original failure.
                failures.append({'phase': 'direct-process', 'error': repr(cleanup)})
        if tree.job is not None:
            failures.extend(tree.job.cleanup_errors)
        try:
            tree.close()
        except BaseException as cleanup:  # noqa: BLE001 -- cleanup must not replace cancellation or the original failure.
            failures.append({'phase': 'ownership-handle', 'error': repr(cleanup)})
        execution['cleanup_confirmed'] = not failures and (proc is None or proc.returncode is not None)
        if failures:
            note = f'Owned process cleanup unconfirmed: {failures}'
            if primary is not None and hasattr(primary, 'add_note'):
                primary.add_note(note)
            _diagnostic(note, execution)
            if primary is None and execution['exit_code'] == 0:
                execution.update(status='cleanup_error', exit_code=1)
        if isinstance(primary, subprocess.TimeoutExpired):
            _diagnostic(f'Test deadline exceeded ({timeout:g}s); evidence: {out_dir}', execution)
        execution['wall_time_seconds'] = time.monotonic() - start
        execution['process_returncode'] = proc.returncode if proc is not None else None
        saved = False
        try:
            execution['manifest_available'] = (out_dir / 'manifest.json').is_file()
            _save_execution(out_dir, execution)
            saved = True
            if execution['manifest_available']:
                from conftest import _write_report
                manifest = json.loads((out_dir / 'manifest.json').read_text(encoding='utf-8'))
                _write_report(out_dir, manifest['run_id'], manifest.get('exit_status'))
        except BaseException as persistence:  # noqa: BLE001 -- preserve prior failure while retaining evidence errors.
            execution['persistence_errors'].append(repr(persistence))
            if primary is None and execution['exit_code'] in (None, 0):
                primary = persistence
                execution.update(status='evidence_error', exit_code=1)
            note = f'Test evidence persistence failed: {persistence!r}'
            if primary is not None and hasattr(primary, 'add_note'):
                primary.add_note(note)
            _diagnostic(note, execution)
            if saved:
                try:
                    _save_execution(out_dir, execution)
                except BaseException as secondary:  # noqa: BLE001 -- metadata recovery must not replace the first error.
                    if primary is not None and hasattr(primary, 'add_note'):
                        primary.add_note(f'Execution metadata update failed: {secondary!r}')
    if isinstance(primary, subprocess.TimeoutExpired):
        return 124
    if primary is not None:
        raise primary
    return execution['exit_code']


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run RPipe tests with TESTING.md filters.')
    parser.add_argument('--level', choices=('unit', 'integration', 'e2e'))
    parser.add_argument('--type', dest='test_type', choices=('location', 'content', 'physical'))
    parser.add_argument('--priority', choices=('p1', 'p2', 'p3'))
    parser.add_argument(
        '--cost-class',
        action='append',
        choices=('c1', 'c2', 'c3', 'c4'),
        default=[],
        help='repeat to allow more than one cost class',
    )
    parser.add_argument('--layer', help='architecture layer marker, e.g. structure_layer')
    parser.add_argument('--module', help='module_* marker without prefix, e.g. control')
    parser.add_argument(
        '--fast',
        action='store_true',
        help='unit + p1 + c1, exclude slow/external/gpu',
    )
    parser.add_argument(
        '--core',
        action='store_true',
        help='unit c1 and c2, exclude slow/external/gpu',
    )
    parser.add_argument('--all', action='store_true', help='run every collected test')
    parser.add_argument('--report-only', type=Path, help='rebuild a saved report without executing tests')
    parser.add_argument('--timeout', type=float, default=1800, help='whole-session deadline in seconds (default: 1800)')
    parser.add_argument('pytest_args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be finite and > 0')

    if args.report_only is not None:
        from conftest import _write_report
        manifest = json.loads((args.report_only / 'manifest.json').read_text(encoding='utf-8'))
        _write_report(args.report_only, manifest['run_id'], manifest.get('exit_status'))
        return 0

    extra = list(args.pytest_args)
    if extra and extra[0] == '--':
        extra = extra[1:]

    parts: list[str] = []
    cost_classes = list(args.cost_class)
    if args.fast:
        parts.append('unit and p1 and not slow and not external and not gpu')
        cost_classes = cost_classes or ['c1']
    elif args.core:
        parts.append('unit and not slow and not external and not gpu')
        cost_classes = cost_classes or ['c1', 'c2']
    elif not args.all:
        if args.level:
            parts.append(args.level)
        if args.test_type:
            parts.append(args.test_type)
        if args.priority:
            parts.append(args.priority)
        if args.layer:
            parts.append(args.layer)
        if args.module:
            name = args.module if args.module.startswith('module_') else f'module_{args.module}'
            parts.append(name)

    cmd = [sys.executable, '-m', 'pytest']
    expr = _expr(parts)
    if expr:
        cmd.extend(['-m', expr])
    for cost_class in cost_classes:
        cmd.extend(['--cost-class', cost_class])
    cmd.extend(extra)
    print(subprocess.list2cmdline(cmd), flush=True)
    # Preserve environment and diagnostics when an embedding process captures
    # this launcher; implicit handle/environment inheritance can be lost there.
    return _run(cmd, args.timeout)


if __name__ == '__main__':
    raise SystemExit(main())

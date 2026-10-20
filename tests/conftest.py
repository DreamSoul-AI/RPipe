"""TESTING.md: require level, objective, priority, cost, and result type."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

LEVELS = frozenset({'unit', 'integration', 'e2e'})
TYPES = frozenset({'location', 'content', 'physical'})
PRIORITIES = frozenset({'p1', 'p2', 'p3'})
COST_CLASSES = frozenset({'c1', 'c2', 'c3', 'c4'})
RESULT_TYPES = frozenset({'categorical', 'numeric'})
RESULT_DETAILS = frozenset({'summary', 'metrics', 'samples'})

REPO = Path(__file__).resolve().parent.parent
RESULTS_ROOT = REPO / '.tmp' / 'test-results'


def _write_manifest(path: Path, manifest: dict) -> None:
    temporary = path.with_name(f'.manifest.json-{uuid.uuid4().hex}.tmp')
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n',
                         encoding='utf-8', errors='backslashreplace')
    os.replace(temporary, path)


def _marker_names(item: pytest.Item) -> set[str]:
    return {marker.name for marker in item.iter_markers()}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        '--cost-class',
        action='append',
        default=[],
        choices=sorted(COST_CLASSES),
        help='keep tests whose cost_class is one of these values',
    )


def _one_marker(item: pytest.Item, name: str) -> pytest.Mark | None:
    found = list(item.iter_markers(name=name))
    if len(found) != 1:
        return None
    return found[0]


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    errors: list[str] = []
    allowed_costs = set(config.getoption('--cost-class') or [])
    kept: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        names = _marker_names(item)
        level = names & LEVELS
        kind = names & TYPES
        priority = names & PRIORITIES
        node = item.nodeid
        if len(level) != 1:
            errors.append(f'{node}: need exactly one of {sorted(LEVELS)}, got {sorted(level)}')
        if len(kind) != 1:
            errors.append(f'{node}: need exactly one of {sorted(TYPES)}, got {sorted(kind)}')
        if len(priority) != 1:
            errors.append(f'{node}: need exactly one of {sorted(PRIORITIES)}, got {sorted(priority)}')
        if 'location' in names and 'unit' not in names:
            errors.append(f'{node}: location must be paired with unit')
        if names & {'integration', 'e2e'} and 'location' in names:
            errors.append(f'{node}: integration/e2e cannot use location')
        cost = _one_marker(item, 'cost')
        if cost is None or cost.kwargs.get('cost_class') not in COST_CLASSES:
            errors.append(f'{node}: need exactly one cost(cost_class=c1|c2|c3|c4)')
        result = _one_marker(item, 'result_type')
        detail = result.kwargs.get('detail') if result is not None else None
        if (
            result is None
            or not result.args
            or result.args[0] not in RESULT_TYPES
            or detail not in RESULT_DETAILS
        ):
            errors.append(
                f'{node}: need result_type(categorical|numeric, detail=summary|metrics|samples)'
            )
        if 'physical' in names and not (names & {'runtime', 'memory'}):
            errors.append(f'{node}: physical needs runtime or memory')
        if allowed_costs and cost is not None and cost.kwargs.get('cost_class') not in allowed_costs:
            deselected.append(item)
        else:
            kept.append(item)
    if errors:
        raise pytest.UsageError('TESTING.md marker contract:\n' + '\n'.join(errors))
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept


def pytest_configure(config: pytest.Config) -> None:
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run_id = os.environ.pop('RPIPE_TEST_RUN_ID', None)
    if run_id is not None:
        if len(run_id) != 32 or any(c not in '0123456789abcdef' for c in run_id):
            raise pytest.UsageError('RPIPE_TEST_RUN_ID must be a lowercase UUID hex string')
    else:
        run_id = f'{stamp}_{uuid.uuid4().hex[:6]}'
    out_dir = RESULTS_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=False)
    if getattr(config.option, 'basetemp', None) is None:
        config.option.basetemp = out_dir / 'pytest'
    config._rpipe_run_id = run_id
    config._rpipe_results_dir = out_dir
    config._rpipe_attempts = {}
    manifest = {
        'run_id': run_id,
        'started_at': datetime.now(timezone.utc).isoformat(),
        'cwd': str(Path.cwd()),
        'python': sys.version.split()[0],
        'platform': platform.platform(),
        'basetemp': str(config.option.basetemp),
        'event_phases': ['setup', 'call', 'teardown'],
        'command': list(config.invocation_params.args),
        'git_sha': os.environ.get('GITHUB_SHA') or _git_sha(),
        'markexpr': getattr(config.option, 'markexpr', '') or '',
        'plan_id': 'ad_hoc',
        'batch_cost_class': None,
        'environment_id': 'local',
        'cost_class': list(getattr(config.option, 'cost_class', None) or []),
    }
    _write_manifest(out_dir / 'manifest.json', manifest)
    (out_dir / 'events.jsonl').write_text('', encoding='utf-8')
    (out_dir / 'artifacts').mkdir(exist_ok=True)


def _git_sha() -> str | None:
    try:
        metadata = REPO / '.git'
        head = metadata / 'HEAD'
        if head.is_file():
            raw = head.read_text(encoding='utf-8').strip()
            if raw.startswith('ref: '):
                ref = metadata / raw[5:].strip()
                raw = ref.read_text(encoding='utf-8').strip() if ref.is_file() else None
            if raw is not None:
                return raw if len(raw) in (40, 64) and all(c in '0123456789abcdefABCDEF' for c in raw) else None
        if not metadata.exists():
            return None
        return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPO, capture_output=True,
                              text=True, check=True, timeout=5).stdout.strip() or None
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return None


def pytest_collection_finish(session: pytest.Session) -> None:
    path = session.config._rpipe_results_dir / 'manifest.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    manifest['collected_test_ids'] = [item.nodeid for item in session.items]
    _write_manifest(path, manifest)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report: pytest.TestReport = outcome.get_result()
    config = item.config
    out_dir: Path = getattr(config, '_rpipe_results_dir', None)
    run_id: str = getattr(config, '_rpipe_run_id', '')
    if out_dir is None:
        return
    attempts = getattr(config, '_rpipe_attempts', None)
    if attempts is None:
        attempts = config._rpipe_attempts = {}
    if report.when == 'setup':
        attempts[item.nodeid] = attempts.get(item.nodeid, 0) + 1
    names = _marker_names(item)
    level = next(iter(names & LEVELS), '')
    kind = next(iter(names & TYPES), '')
    priority = next(iter(names & PRIORITIES), '')
    cost_mark = _one_marker(item, 'cost')
    result_mark = _one_marker(item, 'result_type')
    cost_class = cost_mark.kwargs.get('cost_class') if cost_mark is not None else None
    result_type = result_mark.args[0] if result_mark is not None and result_mark.args else None
    result_detail = result_mark.kwargs.get('detail') if result_mark is not None else None
    status = _status(report, result_type if report.when == 'call' else None)
    duration_ms = int((report.duration or 0) * 1000)
    failure = None
    if report.failed:
        failure = {
            'longrepr': str(report.longrepr)[:4000],
            'when': report.when,
        }
    if status == 'measured':
        verdict = None
        category = None
    elif status in {'passed', 'xpass'}:
        verdict = 'pass'
        category = 'pass'
    elif status in {'failed', 'xfail'}:
        verdict = 'fail'
        category = 'fail'
    else:
        verdict = None
        category = None
    event = {
        'run_id': run_id,
        'attempt': attempts.get(item.nodeid, 1),
        'test_id': item.nodeid,
        'phase': report.when,
        'level': level,
        'type': kind,
        'priority': priority,
        'tags': sorted(names - LEVELS - TYPES - PRIORITIES - {'cost', 'result_type'}),
        'target': item.location[0],
        'result_type': result_type,
        'plan_id': 'ad_hoc',
        'cost_class': cost_class,
        'result_detail': result_detail,
        'environment_id': 'local',
        'verdict': verdict,
        'category': category,
        'metrics': [],
        'cost': {
            'estimated_wall_time_ms': (
                cost_mark.kwargs.get('estimated_wall_time_ms') if cost_mark is not None else None
            ),
            'actual_wall_time_ms': duration_ms,
        },
        'status': status,
        'duration_ms': duration_ms,
        'started_at': datetime.fromtimestamp(report.start, timezone.utc).isoformat(),
        'failure': failure,
        'reason': str(report.longrepr) if report.skipped else None,
        'artifacts': [],
    }
    with (out_dir / 'events.jsonl').open('a', encoding='utf-8', errors='backslashreplace') as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + '\n')


def _status(report: pytest.TestReport, result_type: str | None) -> str:
    if report.skipped:
        return 'xfail' if getattr(report, 'wasxfail', False) else 'skipped'
    if report.passed:
        if getattr(report, 'wasxfail', False):
            return 'xpass'
        if result_type == 'numeric':
            return 'measured'
        return 'passed'
    if getattr(report, 'wasxfail', False):
        return 'xfail'
    return 'failed'


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    out_dir: Path | None = getattr(config, '_rpipe_results_dir', None)
    if out_dir is None or not (out_dir / 'events.jsonl').is_file():
        return
    manifest_path = out_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['exit_status'] = int(exitstatus)
    _write_manifest(manifest_path, manifest)
    _write_report(out_dir, manifest['run_id'], int(exitstatus))


def _write_report(out_dir: Path, run_id: str, exitstatus: int | None) -> None:
    manifest = json.loads((out_dir / 'manifest.json').read_text(encoding='utf-8'))
    raw_lines = (out_dir / 'events.jsonl').read_bytes().splitlines(keepends=True)
    events, truncated = [], None
    for number, raw in enumerate(raw_lines):
        if not raw.strip():
            continue
        try:
            events.append(json.loads(raw.decode('utf-8')))
        except (UnicodeError, json.JSONDecodeError):
            if number != len(raw_lines) - 1 or raw.endswith(b'\n'):
                raise
            truncated = repr(raw)
    attempts = {}
    for event in events:
        phases = attempts.setdefault((event['test_id'], event['attempt']), {})
        phases[event.get('phase', 'call')] = event
    counts: dict[str, int] = {}
    incomplete = []
    for key, phases in attempts.items():
        if manifest.get('event_phases') and 'teardown' not in phases:
            status = 'incomplete'
            incomplete.append(key)
        elif any(event['status'] == 'failed' for event in phases.values()):
            status = 'failed'
        elif any(event['status'] == 'xfail' for event in phases.values()):
            status = 'xfail'
        else:
            result = phases.get('call') or phases.get('setup') or phases.get('teardown')
            if phases.get('teardown', {}).get('status') == 'skipped':
                result = phases['teardown']
            status = result.get('status') or 'unknown'
        counts[status] = counts.get(status, 0) + 1
    seen = {test_id for test_id, attempt in attempts}
    not_run = set(manifest.get('collected_test_ids', [])) - seen
    if not_run:
        counts['not_run'] = len(not_run)
    execution_path = out_dir / 'execution.json'
    execution = json.loads(execution_path.read_text(encoding='utf-8')) if execution_path.is_file() else {}
    lines = [
        '# Test report',
        '',
        f'- run_id: `{run_id}`',
        '- plan_id: `ad_hoc`',
        f'- exit_status: {exitstatus if exitstatus is not None else "unknown"}',
        f'- launcher_status: {execution.get("status", "unknown")}',
        f'- launcher_exit_code: {execution.get("exit_code", "unknown")}',
        f'- cleanup_confirmed: {execution.get("cleanup_confirmed", "unknown")}',
        f'- collection_saved: {"collected_test_ids" in manifest}',
        f'- phase_coverage: {"setup/call/teardown" if manifest.get("event_phases") else "legacy (unknown)"}',
        '',
        '| status | count |',
        '|---|---:|',
    ]
    for status in sorted(counts):
        lines.append(f'| {status} | {counts[status]} |')
    lines.append('')
    lines.append('数值测量完成记 `measured`，质量判定为 null。通过率的分母不含 skipped 与 measured。')
    lines.append('计数按尝试；缺 teardown 记 incomplete，未开始的选中节点记 not_run。')
    if truncated is not None:
        lines.extend(['', 'Truncated final event (not counted); original bytes retained:', '',
                      '```text', truncated, '```'])
    for test_id, attempt in incomplete:
        lines.append(f'- incomplete: `{test_id}`, attempt {attempt}')
    for test_id in sorted(not_run):
        lines.append(f'- not_run: `{test_id}`')
    for cleanup in execution.get('cleanup_errors', []):
        lines.extend(['', '```text', f'Cleanup unconfirmed: {cleanup}', '```'])
    for diagnostic in execution.get('diagnostic_errors', []):
        lines.extend(['', '```text', f'Diagnostic output failed: {diagnostic}', '```'])
    for persistence in execution.get('persistence_errors', []):
        lines.extend(['', '```text', f'Evidence persistence failed: {persistence}', '```'])
    for event in events:
        reason = (event.get('failure') or {}).get('longrepr') or event.get('reason')
        if reason:
            lines.extend(['', f"- `{event['test_id']}` ({event.get('phase', 'unknown')}, {event['status']})",
                          '', '```text', reason, '```'])
    lines.append('')
    (out_dir / 'report.md').write_text('\n'.join(lines), encoding='utf-8', errors='backslashreplace')


@pytest.fixture
def stream_close_fault(monkeypatch):
    def inject(select, primary, secondary, *, phase='write'):
        original = Path.open
        handles, calls = [], []
        class Stream:
            def __init__(self, raw):
                self.raw = raw
            def __getattr__(self, name):
                return getattr(self.raw, name)
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                self.close()
            def write(self, value):
                calls.append('write')
                if phase == 'write' and primary is not None:
                    self.raw.write(value[:3])
                    self.raw.flush()
                    raise primary
                return self.raw.write(value)
            def flush(self):
                calls.append('flush')
                self.raw.flush()
                if phase == 'flush' and primary is not None:
                    raise primary
            def close(self):
                calls.append('close')
                self.raw.close()
                if secondary is not None:
                    raise secondary
        def opening(path, mode='r', *args, **kwargs):
            raw = original(path, mode, *args, **kwargs)
            if select(path, mode):
                handles.append(raw)
                return Stream(raw)
            return raw
        monkeypatch.setattr(Path, 'open', opening)
        return handles, calls
    return inject


@pytest.fixture(params=['read-io', 'cancel', 'body-programming', 'close-io', 'close-programming', 'success'])
def reader_exit_case(request):
    from types import SimpleNamespace
    kind = request.param
    primary = (OSError('first read IO') if kind == 'read-io' else
               KeyboardInterrupt('first read cancellation') if kind == 'cancel' else
               ValueError('first read programming error') if kind == 'body-programming' else None)
    secondary = (RuntimeError('unexpected close programming error') if kind == 'close-programming' else
                 None if kind == 'success' else OSError('second close IO'))
    if kind == 'close-programming':
        primary = OSError('first read IO')
    expected = secondary if kind == 'close-programming' or primary is None else primary
    return SimpleNamespace(kind=kind, primary=primary, secondary=secondary, expected=expected)


@pytest.fixture
def reader_close_fault(monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def inject(selected, case, selected_mode):
        native = Path.open
        handles, closes = [], []
        class Stream:
            def __init__(self, raw):
                self.raw = raw
            def __getattr__(self, name):
                return getattr(self.raw, name)
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                self.close()
            def read(self, *args, **kwargs):
                if case.primary is not None:
                    raise case.primary
                return self.raw.read(*args, **kwargs)
            def readline(self, *args, **kwargs):
                if case.primary is not None:
                    raise case.primary
                return self.raw.readline(*args, **kwargs)
            def close(self):
                closes.append(self.raw)
                self.raw.close()
                if case.secondary is not None:
                    raise case.secondary
        def opening(path, mode='r', *args, **kwargs):
            raw = native(path, mode, *args, **kwargs)
            if path == selected and mode == selected_mode:
                handles.append(raw)
                return Stream(raw)
            return raw
        with monkeypatch.context() as scope:
            scope.setattr(Path, 'open', opening)
            yield handles, closes
    return inject
